"""agent-browser-backed renderer.

Owns a single agent-browser session keyed by name; opens a URL, sets the
viewport, and exposes `eval_js` for downstream JS extraction. agent-browser
runs as a daemon, so subsequent `eval` calls reuse the same Chrome instance.

Anti-bot resistance comes for free (real system Chrome > headless Chromium).
Phase 3 fallback is now a `--provider` switch on the same CLI rather than a
parallel renderer stack.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


VIEWPORT = (1440, 900)
DEFAULT_SESSION = "design-from-url"


class RenderError(RuntimeError):
    """Raised when an agent-browser command exits non-zero or returns an error envelope."""


@dataclass(frozen=True)
class RenderInfo:
    final_url: str
    page_title: str
    html_size: int  # length of document.documentElement.outerHTML in chars


class BrowserSession:
    """Thin wrapper around `agent-browser --session <name> ...` subprocess calls.

    Sessions persist across CLI invocations until `close()` runs; each command
    is a fresh subprocess (one-shot), so all state lives in the daemon.
    """

    def __init__(self, session_name: str = DEFAULT_SESSION):
        self.session = session_name
        self._bin = shutil.which("agent-browser")
        if self._bin is None:
            raise RenderError(
                "`agent-browser` not found on PATH. Install via "
                "`brew install agent-browser` or `npm install -g agent-browser`."
            )

    # ---- subprocess plumbing ----

    def _run(
        self,
        *args: str,
        stdin: str | None = None,
        timeout_s: int = 60,
        json_envelope: bool = False,
    ) -> dict[str, Any] | str:
        cmd = [self._bin, "--session", self.session, *args]
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(
                f"agent-browser timed out after {timeout_s}s: {' '.join(args)}"
            ) from exc

        if proc.returncode != 0:
            raise RenderError(
                f"agent-browser {' '.join(args)} exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            )

        if json_envelope:
            try:
                envelope = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise RenderError(
                    f"agent-browser returned non-JSON output: {proc.stdout[:200]!r}"
                ) from exc
            if not envelope.get("success", False):
                raise RenderError(
                    f"agent-browser {' '.join(args)} reported failure: "
                    f"{envelope.get('error')}"
                )
            return envelope
        return proc.stdout

    # ---- high-level operations ----

    def set_viewport(self, width: int, height: int) -> None:
        self._run("set", "viewport", str(width), str(height))

    def open_url(self, url: str, *, timeout_s: int = 30) -> None:
        # `open` waits for load; we add an explicit ms wait for late JS-driven
        # content to settle. 500ms is enough for most sites; consent dismissal
        # adds another stability check downstream.
        self._run("open", url, timeout_s=timeout_s)
        self._run("wait", "500")

    def eval_js(self, script: str, *, timeout_s: int = 60) -> Any:
        envelope = self._run(
            "eval", "--stdin", "--json",
            stdin=script, timeout_s=timeout_s, json_envelope=True,
        )
        return envelope["data"]["result"]

    def click(self, selector: str, *, timeout_s: int = 5) -> None:
        self._run("click", selector, timeout_s=timeout_s)

    def get_url(self) -> str:
        out = self._run("get", "url")
        return str(out).strip()

    def get_title(self) -> str:
        out = self._run("get", "title")
        return str(out).strip()

    def screenshot(self, output_path: str, *, timeout_s: int = 30) -> None:
        """Capture viewport screenshot to `output_path` (PNG).

        Wraps `agent-browser screenshot <path>`. agent-browser auto-creates
        the parent directory for the path if it doesn't exist.
        """
        self._run("screenshot", output_path, timeout_s=timeout_s)

    def set_color_scheme(self, scheme: str) -> None:
        """Toggle the page's effective color scheme (Phase 3a 3a.5b).

        Wraps `agent-browser set media <scheme>` — the verified CLI shape
        from 3a.1 probe (NOT `set color-scheme`, which doesn't exist).

        State is sticky across subsequent `eval_js` calls in the same
        session AND reversible. `scheme` must be 'light' or 'dark'.
        """
        if scheme not in ("light", "dark"):
            raise ValueError(
                f"scheme must be 'light' or 'dark', got {scheme!r}"
            )
        self._run("set", "media", scheme)

    def close(self) -> None:
        # Close just this session's tab/context, leaving any other sessions
        # (and any user-visible Chrome windows) untouched.
        try:
            self._run("close", timeout_s=10)
        except RenderError:
            pass  # idempotent — already closed is fine


@contextmanager
def open_session(
    url: str,
    *,
    session_name: str = DEFAULT_SESSION,
    viewport: tuple[int, int] = VIEWPORT,
    timeout_s: int = 30,
    dismiss_consent_banners: bool = True,
) -> Iterator[tuple[BrowserSession, RenderInfo]]:
    """Open a URL in a managed agent-browser session and yield (session, info).

    Lifecycle: spawn-or-reuse daemon → set viewport → navigate → optional
    consent dismissal → yield. On exit, the session is closed regardless of
    caller success.
    """
    from design_from_url.consent import dismiss_consent

    session = BrowserSession(session_name=session_name)
    try:
        session.set_viewport(*viewport)
        session.open_url(url, timeout_s=timeout_s)

        if dismiss_consent_banners:
            dismiss_consent(session)

        # Probe page identity for run report. Page HTML size is used as a
        # health check (Phase 3.1 fallback trigger threshold).
        size = session.eval_js("document.documentElement.outerHTML.length")
        info = RenderInfo(
            final_url=session.get_url(),
            page_title=session.get_title(),
            html_size=int(size or 0),
        )
        yield session, info
    finally:
        session.close()
