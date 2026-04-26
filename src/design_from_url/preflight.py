"""Preflight check: verify @google/design.md is reachable via npm exec.

Uses `npm exec --package=<pkg> -- <bin> --version` rather than `npx <pkg>
--version` because npm 11.13+ rejects the latter form ("Unknown command")
when the package binary name differs from the package name (binary is
`design.md`, package is `@google/design.md`). See design.md D17 for details.

Phase 2 additions: `LintFinding` / `LintResult` dataclasses, structured
lint API, and severity+message classification (no `rule` field exists in
lint v0.1.1 output — see design.md D5).
"""

from __future__ import annotations

import json
import re
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


@dataclass(frozen=True)
class LintFinding:
    """A single finding from `design.md lint --format json`.

    Lint v0.1.1 output shape: {severity, path?, message}. There is NO `rule`
    field — classification is by severity + message pattern (see classify()).
    """
    severity: str        # "error" | "warning" | "info"
    path: str            # e.g. "colors.primary"; empty string when omitted
    message: str


@dataclass(frozen=True)
class LintResult:
    exit_code: int
    errors: int
    warnings: int
    infos: int
    findings: tuple[LintFinding, ...]
    raw_stdout: str = ""  # preserved for debugging / re-classification


# Message patterns used to categorize findings. Lint v0.1.1 has no machine-
# readable `rule` field, so we match on message substrings.
#
# Schema-fixable: errors that the schema_fixer can correct deterministically
# (re-emit YAML, swap raw hex for ref, resolve broken ref via ΔE-nearest).
_SCHEMA_PATTERNS = (
    re.compile(r"is not a valid color"),
    re.compile(r"Reference .* does not resolve"),
    re.compile(r"Unexpected error during model building"),
    re.compile(r"No YAML content found"),
)
# Prose-fixable: warnings that should be addressed by re-asking the LLM
# (e.g., add missing token, drop orphan).
_PROSE_PATTERNS = (
    re.compile(r"is defined but never referenced"),
    re.compile(r"No '[a-z]+' (color|typography) defined"),
    re.compile(r"No typography tokens defined"),
)


def classify(
    findings: tuple[LintFinding, ...],
) -> tuple[list[LintFinding], list[LintFinding]]:
    """Split findings into (schema_findings, prose_findings).

    Severity rules (default-safe):
      - error → schema (must fix)
      - warning → match against _PROSE_PATTERNS first; default to prose
        (re-asking LLM is safer than silent fixer overwrites for unknown rules)
      - info → ignored (returned in neither bucket)
    """
    schema: list[LintFinding] = []
    prose: list[LintFinding] = []
    for f in findings:
        if f.severity == "error":
            schema.append(f)
        elif f.severity == "warning":
            if any(p.search(f.message) for p in _SCHEMA_PATTERNS):
                schema.append(f)
            else:
                prose.append(f)
        # info → drop
    return schema, prose


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

    Backward-compat shim for Phase 1 callers. Phase 2 should prefer
    `lint_design_md_structured()` which parses JSON into a `LintResult`.
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


def parse_lint_json(stdout: str, exit_code: int) -> LintResult:
    """Parse `design.md lint --format json` stdout into a LintResult.

    On JSON decode failure (e.g., npm exec returned a non-JSON error),
    returns a single synthesized error finding so callers don't have to
    branch on parsing failures.
    """
    try:
        data = json.loads(stdout) if stdout.strip() else {"findings": [], "summary": {}}
    except json.JSONDecodeError as exc:
        return LintResult(
            exit_code=exit_code,
            errors=1, warnings=0, infos=0,
            findings=(LintFinding(
                severity="error", path="",
                message=f"lint output not valid JSON: {exc}",
            ),),
            raw_stdout=stdout,
        )
    findings = tuple(
        LintFinding(
            severity=f.get("severity", "error"),
            path=f.get("path", ""),
            message=f.get("message", ""),
        )
        for f in data.get("findings", [])
    )
    summary = data.get("summary", {})
    return LintResult(
        exit_code=exit_code,
        errors=int(summary.get("errors", 0)),
        warnings=int(summary.get("warnings", 0)),
        infos=int(summary.get("infos", 0)),
        findings=findings,
        raw_stdout=stdout,
    )


def lint_design_md_structured(
    file_path: str, *, timeout_s: int = 60,
) -> LintResult:
    """Run `design.md lint --format json <file>` and return a parsed LintResult.

    Used by Phase 2.5 self-lint loop. On infrastructure failures (npm missing,
    timeout) returns a LintResult with exit_code != 0 and a synthesized error
    finding so callers see a uniform shape.
    """
    cmd = _build_command(["lint", "--format", "json", file_path])
    if cmd is None:
        return LintResult(
            exit_code=127, errors=1, warnings=0, infos=0,
            findings=(LintFinding(severity="error", path="",
                                  message="npm not on PATH"),),
        )
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return LintResult(
            exit_code=124, errors=1, warnings=0, infos=0,
            findings=(LintFinding(severity="error", path="",
                                  message=f"lint timed out after {timeout_s}s"),),
        )
    return parse_lint_json(proc.stdout, proc.returncode)
