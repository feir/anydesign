"""Dark mode dual-run support (Phase 3a 3a.5).

Capabilities:

- `check_agent_browser_version()` — preflight; reads `agent-browser --version`
  and compares to MIN_AGENT_BROWSER_VERSION via packaging.version.
- `_probe_dark_mode_support()` — runtime probe verifying `set media`
  actually flips colors against a synthetic data: URL. Cached per-process.
- `diff_registries(light, dark)` — colors changed between the two runs.
- `build_dark_section(diff)` — markdown table for DESIGN.md.

`DarkModeUnsupported` is raised when capability check fails — the CLI
layer translates to a clear stderr message + exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from packaging.version import InvalidVersion, Version

from design_from_url.constants import MIN_AGENT_BROWSER_VERSION


class DarkModeUnsupported(RuntimeError):
    """Raised when the environment can't perform dark-mode dual-run."""


def check_agent_browser_version() -> str:
    """Return agent-browser version string. Raise DarkModeUnsupported if
    missing or older than MIN_AGENT_BROWSER_VERSION."""
    binary = shutil.which("agent-browser")
    if binary is None:
        raise DarkModeUnsupported(
            "agent-browser not found on PATH (required for --with-dark). "
            "Install via `brew install agent-browser`."
        )
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.SubprocessError as exc:
        raise DarkModeUnsupported(
            f"agent-browser --version failed: {exc}"
        ) from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()[:200]
        raise DarkModeUnsupported(
            f"agent-browser --version exited {proc.returncode}: {msg}"
        )
    raw = (proc.stdout or proc.stderr).strip()
    # `agent-browser --version` typically prints "agent-browser 0.26.0"
    # or just "0.26.0"; extract the trailing token.
    parts = raw.split()
    candidate = (parts[-1] if parts else raw).lstrip("v")
    try:
        v = Version(candidate)
    except InvalidVersion as exc:
        raise DarkModeUnsupported(
            f"could not parse agent-browser version from {raw!r}"
        ) from exc
    minimum = Version(MIN_AGENT_BROWSER_VERSION)
    if v < minimum:
        raise DarkModeUnsupported(
            f"agent-browser {v} is older than the required {minimum}; "
            f"upgrade via `brew upgrade agent-browser` to enable --with-dark."
        )
    return str(v)


# Synthetic single-page fixture with a CSS variable that flips on
# `prefers-color-scheme: dark`. Used by the runtime probe.
_PROBE_HTML = (
    "data:text/html;charset=utf-8,"
    "%3Chtml%3E%3Chead%3E%3Cstyle%3E"
    ":root%7B--c:red%7D"
    "%40media(prefers-color-scheme:dark)%7B:root%7B--c:blue%7D%7D"
    "%3C/style%3E%3C/head%3E%3Cbody%3E"
    "%3Cdiv%20id=%22x%22%20style=%22color:var(--c)%22%3EA%3C/div%3E"
    "%3C/body%3E%3C/html%3E"
)


# Memoize successes only. A transient probe failure (e.g. concurrent agent-browser
# session collision) must not poison subsequent calls in the same process.
_PROBE_RESULT_CACHE: bool | None = None


def _probe_dark_mode_support() -> bool:
    """Verify `set media` actually flips colors. Cached on success only.

    Fail-closed: any error returns False (we'd rather refuse the dark
    run than silently produce a wrong result). Session name is PID-scoped
    so concurrent invocations on the same host don't collide.
    """
    global _PROBE_RESULT_CACHE
    if _PROBE_RESULT_CACHE is True:
        return True

    from design_from_url.renderer import BrowserSession
    session_name = f"design-from-url-darkprobe-{os.getpid()}"
    try:
        session = BrowserSession(session_name=session_name)
    except Exception:
        return False
    try:
        try:
            session.open_url(_PROBE_HTML, timeout_s=10)
            session.set_color_scheme("light")
            light = session.eval_js(
                "getComputedStyle(document.getElementById('x')).color"
            )
            session.set_color_scheme("dark")
            dark = session.eval_js(
                "getComputedStyle(document.getElementById('x')).color"
            )
        finally:
            session.close()
    except Exception:
        return False
    ls = str(light or "").strip()
    ds = str(dark or "").strip()
    result = bool(ls and ds and ls != ds)
    if result:
        _PROBE_RESULT_CACHE = True
    return result


def _coerce_color_map(registry: object) -> dict[str, str]:
    """Pull a flat token_name → value map out of registry's colors.

    Handles both dict-shaped and list-of-Token-shaped representations
    (the aggregator may emit either depending on call site).
    """
    if registry is None:
        return {}
    colors = None
    if isinstance(registry, dict):
        colors = registry.get("colors")
    else:
        colors = getattr(registry, "colors", None)
    if isinstance(colors, dict):
        return {str(k): str(v) for k, v in colors.items()}
    # Real Registry.colors is `tuple` (frozen dataclass), not list — accept both
    # (and any other sequence). isinstance(colors, list) alone would silently
    # return {} for the real type, masking diff failures behind synthetic tests.
    if isinstance(colors, (list, tuple)):
        out: dict[str, str] = {}
        for c in colors:
            if hasattr(c, "name"):
                out[str(c.name)] = str(getattr(c, "value", ""))
            elif isinstance(c, dict):
                out[str(c.get("name", "?"))] = str(c.get("value", ""))
        return out
    return {}


def diff_registries(light: object, dark: object) -> dict[str, dict[str, str]]:
    """Return colors that changed between light and dark registries.

    Output: {token_name: {"light": <val>, "dark": <val>}} for tokens
    whose value differs or are present in only one side. Tokens missing
    from one side render as the literal string "(missing)" so the
    markdown table is still readable.
    """
    L = _coerce_color_map(light)
    D = _coerce_color_map(dark)
    keys = set(L) | set(D)
    out: dict[str, dict[str, str]] = {}
    for k in sorted(keys):
        lv = L.get(k, "(missing)")
        dv = D.get(k, "(missing)")
        if lv != dv:
            out[k] = {"light": lv, "dark": dv}
    return out


def preflight() -> None:
    """Run both capability checks. Raise DarkModeUnsupported on any failure.

    Order: cheap version check first; only invoke the runtime probe (which
    spawns a Chrome session) when version passes.
    """
    check_agent_browser_version()
    if not _probe_dark_mode_support():
        raise DarkModeUnsupported(
            "agent-browser `set media` probe failed at runtime — "
            "color scheme did not flip. Try `agent-browser kill-all` or "
            "upgrade agent-browser."
        )


def build_dark_section(diff: dict[str, dict[str, str]]) -> str:
    """Render the diff as a markdown subsection. Empty diff → empty string."""
    if not diff:
        return ""
    lines = ["## Dark Mode", ""]
    lines.append("| Token | Light | Dark |")
    lines.append("|-------|-------|------|")
    for token, vals in diff.items():
        lines.append(f"| `{token}` | `{vals['light']}` | `{vals['dark']}` |")
    lines.append("")
    return "\n".join(lines)
