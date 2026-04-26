"""Tests for prose_retry.py — Phase 2.5c."""

from __future__ import annotations

from design_from_url.preflight import LintFinding
from design_from_url.prose_retry import (
    build_retry_prompt, format_findings_feedback, replace_overview_section,
)


def test_format_findings_feedback_renders_bullets():
    findings = [
        LintFinding("warning", "colors.unused", "'unused' defined but never referenced."),
        LintFinding("warning", "typography", "No typography tokens defined."),
    ]
    out = format_findings_feedback(findings)
    assert "[warning] colors.unused" in out
    assert "never referenced" in out
    assert "[warning] typography" in out


def test_format_findings_feedback_empty():
    assert "no specific findings" in format_findings_feedback([])


def test_build_retry_prompt_includes_all_parts():
    base = "Generate Overview prose."
    prev = "Old overview text."
    findings = [LintFinding("warning", "x", "y")]
    out = build_retry_prompt(base, prev, findings)
    assert base in out
    assert "Retry feedback" in out
    assert "Old overview text" in out
    assert "[warning] x" in out


def test_replace_overview_section_swaps_body():
    md = (
        "# Site\n\n"
        "## Overview\n\n"
        "<!-- LLM_PLACEHOLDER:overview -->\n\n"
        "## Colors\n\n"
        "Foo bar.\n"
    )
    new = replace_overview_section(md, "Brand identity is bold and minimalist.")
    assert "Brand identity is bold and minimalist." in new
    assert "<!-- LLM_PLACEHOLDER:overview -->" not in new
    # Other sections preserved
    assert "## Colors\n\nFoo bar." in new


def test_replace_overview_section_with_existing_prose():
    """Replace prose that already had previous LLM output (retry path)."""
    md = (
        "## Overview\n\n"
        "Previous prose was poor.\n\n"
        "## Colors\n\n"
        "x\n"
    )
    new = replace_overview_section(md, "Improved prose.")
    assert "Improved prose." in new
    assert "Previous prose was poor." not in new


def test_replace_overview_section_returns_unchanged_when_missing():
    """No Overview section → no-op (don't mutate)."""
    md = "# Just a header\n\nSome body.\n"
    assert replace_overview_section(md, "ignored") == md
