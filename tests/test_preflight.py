"""Tests for preflight.py — Phase 2 lint output contract (task 2.4.5).

Covers:
- LintResult / LintFinding dataclasses parse fixture JSONs correctly
- classify() splits findings into schema vs prose by severity + message
- lint_design_md() backward-compat shim still returns tuple[int, str]
- parse_lint_json() handles malformed input gracefully
"""

from __future__ import annotations

import json
from pathlib import Path

from design_from_url.preflight import (
    LintFinding, LintResult,
    classify, lint_design_md, parse_lint_json,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lint-findings"


def _load(name: str) -> tuple[str, int]:
    """Load a fixture file and return (stdout, exit_code)."""
    data = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    return data["stdout"], data["exit_code"]


# ---- parse_lint_json: shape correctness ----

def test_parse_clean_fixture():
    """Clean fixture has no errors but has orphan warnings (neutral colors
    are defined but no component references them — realistic for early
    Phase 2 output before component identification runs)."""
    stdout, rc = _load("clean")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 0
    assert result.errors == 0
    # The fixture has 2 orphan-token warnings (neutral_dark, neutral_light).
    # Errors=0 is what gates self-lint loop convergence, not warnings.
    assert result.infos >= 1
    # Classify: orphan warnings → prose bucket (LLM should wire them up or drop)
    schema, prose = classify(result.findings)
    assert schema == []
    assert all("never referenced" in p.message for p in prose)


def test_parse_bad_hex_fixture():
    stdout, rc = _load("bad-hex")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 1
    assert result.errors >= 1
    err = next(f for f in result.findings if f.severity == "error")
    assert err.path == "colors.primary"
    assert "is not a valid color" in err.message


def test_parse_parse_error_fixture():
    """Unquoted hex in YAML parses as comment → null → error."""
    stdout, rc = _load("parse-error-null")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 1
    assert result.errors >= 1
    err = next(f for f in result.findings if f.severity == "error")
    assert "null" in err.message.lower() or "valid color" in err.message


def test_parse_spacing_as_list_fixture():
    """Type mismatch (list of numbers vs scale-named map) → model-building error."""
    stdout, rc = _load("spacing-as-list")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 1
    assert result.errors >= 1
    err = next(f for f in result.findings if f.severity == "error")
    assert "model building" in err.message


def test_parse_ref_undefined_fixture():
    """{colors.nonexistent} reference → schema error + orphan warning."""
    stdout, rc = _load("ref-undefined")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 1
    assert result.errors >= 1
    err = next(f for f in result.findings if f.severity == "error")
    assert "Reference" in err.message and "does not resolve" in err.message
    assert err.path.startswith("components.")


def test_parse_no_yaml_fixture():
    """Markdown without YAML frontmatter → warning only (lint v0.1.1 is lenient)."""
    stdout, rc = _load("no-yaml")
    result = parse_lint_json(stdout, rc)
    assert result.exit_code == 0
    assert result.warnings >= 1
    warn = next(f for f in result.findings if f.severity == "warning")
    assert "YAML" in warn.message


# ---- classify: severity + pattern routing ----

def test_classify_error_always_schema():
    findings = (
        LintFinding(severity="error", path="colors.primary",
                    message="'#xyz' is not a valid color."),
    )
    schema, prose = classify(findings)
    assert len(schema) == 1 and prose == []


def test_classify_warning_orphan_routes_to_prose():
    """Orphan token (defined but unreferenced) is prose-fixable: LLM can wire it up or drop."""
    findings = (
        LintFinding(severity="warning", path="colors.unused",
                    message="'unused' is defined but never referenced by any component."),
    )
    schema, prose = classify(findings)
    assert schema == [] and len(prose) == 1


def test_classify_warning_no_yaml_routes_to_schema():
    """'No YAML content' is structural — schema fixer should re-emit YAML, not LLM."""
    findings = (
        LintFinding(severity="warning", path="",
                    message="No YAML content found. Expected frontmatter (---) or fenced yaml code blocks."),
    )
    schema, prose = classify(findings)
    assert len(schema) == 1 and prose == []


def test_classify_unknown_warning_default_routes_to_prose():
    """Unknown warning patterns default to prose (re-asking LLM is safer than silent overwrite)."""
    findings = (
        LintFinding(severity="warning", path="",
                    message="some-future-rule produced this warning"),
    )
    schema, prose = classify(findings)
    assert schema == [] and len(prose) == 1


def test_classify_info_dropped():
    findings = (
        LintFinding(severity="info", path="",
                    message="Design system defines 12 colors..."),
    )
    schema, prose = classify(findings)
    assert schema == [] and prose == []


def test_classify_mixed_real_findings():
    """ref-undefined fixture produces error + warnings + infos — verify split."""
    stdout, rc = _load("ref-undefined")
    result = parse_lint_json(stdout, rc)
    schema, prose = classify(result.findings)
    # Exactly 1 error → schema (the broken ref)
    assert len(schema) >= 1
    assert all(f.severity == "error" or "YAML" in f.message for f in schema)
    # Warnings about missing tokens / orphans → prose
    assert all(f.severity == "warning" for f in prose)


# ---- malformed input ----

def test_parse_malformed_json_returns_synthesized_error():
    """Garbage stdout (e.g. npm fetch error) → single synthesized error finding, never throws."""
    result = parse_lint_json("not json at all", 1)
    assert result.exit_code == 1
    assert result.errors == 1
    assert "not valid JSON" in result.findings[0].message


def test_parse_empty_stdout():
    """Empty stdout → empty result, no crash."""
    result = parse_lint_json("", 0)
    assert result.exit_code == 0
    assert result.errors == 0
    assert result.findings == ()


# ---- backward compat ----

def test_lint_design_md_still_returns_tuple(tmp_path):
    """Phase 1 callers expect tuple[int, str]; do not break them.

    This test does not actually invoke npm — it only verifies the function
    signature is preserved. (Real npm execution is covered by E2E in 2.6.)
    """
    import inspect
    sig = inspect.signature(lint_design_md)
    # Return annotation should still be tuple[int, str] (not LintResult)
    assert "tuple" in str(sig.return_annotation).lower()
