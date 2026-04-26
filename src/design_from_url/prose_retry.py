"""Prose retry — Phase 2.5c.

When `design.md lint` flags prose-level issues (orphan tokens, missing
typography, etc.), regenerate the relevant section by re-asking the LLM
with the previous output + finding feedback. Vision-bearing call:
propagates `LLMUnavailable` per design.md D1.

Scope: regenerates the OVERVIEW section only in this iteration. Component
+ Do's regeneration would require multi-section LLM round-trips and is
deferred to a future Phase 2.x — the self-lint loop just retries overview
prose because that's where most prose-pattern findings cluster (orphan
tokens to wire in or describe).
"""

from __future__ import annotations

import re

from design_from_url.preflight import LintFinding


PLACEHOLDER_OVERVIEW = "<!-- LLM_PLACEHOLDER:overview -->"
_OVERVIEW_SECTION_RE = re.compile(
    r"(## Overview\n\n)(.*?)(\n+## )",
    re.DOTALL,
)


def format_findings_feedback(findings: list[LintFinding]) -> str:
    """Render prose findings as human-readable feedback for the LLM retry prompt."""
    if not findings:
        return "(no specific findings)"
    bullets = []
    for f in findings:
        path = f.path or "(no path)"
        bullets.append(f"- [{f.severity}] {path}: {f.message}")
    return "\n".join(bullets)


def build_retry_prompt(
    base_prompt: str,
    previous_output: str,
    findings: list[LintFinding],
) -> str:
    """Wrap the base prompt with retry-specific feedback.

    Format:
        <base_prompt>

        ---

        # Retry feedback

        Your previous output produced the following lint findings.
        Address each one in the new output:

        <findings>

        Previous output (for reference):

        <previous_output>
    """
    return (
        f"{base_prompt}\n\n"
        f"---\n\n"
        f"# Retry feedback\n\n"
        f"Your previous output produced the following lint findings. "
        f"Address each one in the new output:\n\n"
        f"{format_findings_feedback(findings)}\n\n"
        f"Previous output (for reference):\n\n"
        f"```\n{previous_output}\n```\n"
    )


def replace_overview_section(design_md_text: str, new_overview: str) -> str:
    """Replace the Overview section's body in DESIGN.md text.

    Matches `## Overview\\n\\n<body>\\n+## ` and substitutes `<body>` with
    `new_overview`. If the original used the LLM_PLACEHOLDER token,
    the placeholder gets replaced too (treated as the section body).

    Returns the modified text. If no Overview section exists, returns
    input unchanged.
    """
    new_overview = new_overview.strip()

    def _sub(m: re.Match) -> str:
        return f"{m.group(1)}{new_overview}{m.group(3)}"

    new_text, count = _OVERVIEW_SECTION_RE.subn(_sub, design_md_text, count=1)
    if count == 0:
        return design_md_text
    return new_text
