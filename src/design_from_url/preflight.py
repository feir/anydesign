"""Preflight check: verify @google/design.md is reachable via npm exec.

Uses `npm exec --package=<pkg> -- <bin> --version` rather than `npx <pkg>
--version` because npm 11.13+ rejects the latter form ("Unknown command")
when the package binary name differs from the package name (binary is
`design.md`, package is `@google/design.md`). See design.md D17 for details.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from design_from_url.constants import DESIGN_MD_NPM_PACKAGE


# Binary name shipped by @google/design.md (per `npm view ... bin`).
_DESIGN_MD_BIN = "design.md"


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str = ""
    version_output: str = ""


def _build_command(bin_args: list[str]) -> list[str] | None:
    """Build `npm exec --yes --package=<pkg> -- <bin_args>` or None if no npm."""
    npm = shutil.which("npm")
    if npm is None:
        return None
    return [
        npm, "exec", "--yes",
        f"--package={DESIGN_MD_NPM_PACKAGE}",
        "--", _DESIGN_MD_BIN, *bin_args,
    ]


def check_npx_design_md(timeout_s: int = 60) -> PreflightResult:
    """Probe `design.md spec` via npm exec to validate the CLI is installable.

    First call may take ~30s while npm fetches the package; subsequent calls
    hit cache and finish in <2s. Network is required for the first run.
    """
    cmd = _build_command(["spec"])
    if cmd is None:
        return PreflightResult(ok=False, reason="`npm` not found on PATH")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return PreflightResult(
            ok=False,
            reason=f"`npm exec ... {_DESIGN_MD_BIN} spec` timed out after {timeout_s}s",
        )
    except OSError as exc:
        return PreflightResult(ok=False, reason=f"npm exec invocation failed: {exc}")

    if proc.returncode != 0:
        return PreflightResult(
            ok=False,
            reason=(
                f"`npm exec ... {_DESIGN_MD_BIN} spec` exited "
                f"{proc.returncode}: {proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}"
            ),
        )
    # spec output is multi-line markdown; first line is a generated-from
    # comment. We just confirm output non-empty.
    out = proc.stdout.strip()
    return PreflightResult(
        ok=True,
        version_output=f"{len(out)} chars of spec returned",
    )


def lint_design_md(file_path: str, *, timeout_s: int = 60) -> tuple[int, str]:
    """Run `design.md lint <file>` and return (exit_code, json_or_text_output).

    Caller is responsible for parsing JSON output and acting on findings.
    Used by Phase 2.5 self-lint loop.
    """
    cmd = _build_command(["lint", file_path])
    if cmd is None:
        return (127, "npm not on PATH")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return (124, "lint timed out")
    return (proc.returncode, proc.stdout)
