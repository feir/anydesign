"""Tests for run_report.py — Phase 2.5 observability + D6.1 enum mapping."""

from __future__ import annotations

import json

import pytest

from design_from_url.run_report import (
    STATUS_MAP, FixerAction, RunReport,
)


def _report() -> RunReport:
    return RunReport(
        url="https://x.com",
        extracted_at="2026-04-26T00:00:00Z",
        registry_size={"colors": 12, "typography": 6, "spacing": 5, "rounded": 2},
        llm_model="local/gemma4:26b",
    )


# ---- D6.1 enum mapping (M6 fix) ----

def test_status_map_pass_when_no_degraded_reason():
    assert STATUS_MAP[None] == ("PASS", 0)


def test_status_map_omx_failover_is_hard_fail_exit_1():
    """Phase 3a (BREAKING): HARD_FAIL exit code flipped 2 → 1."""
    assert STATUS_MAP["omx_failover"] == ("HARD_FAIL", 1)


def test_status_map_required_field_unresolvable_is_hard_fail_exit_1():
    assert STATUS_MAP["required_field_unresolvable"] == ("HARD_FAIL", 1)


def test_status_map_prose_retry_exhausted_is_degraded():
    assert STATUS_MAP["prose_retry_exhausted"] == ("DEGRADED", 2)


@pytest.mark.parametrize("reason", [
    "url_parse_failed", "render_timeout", "lint_cli_missing", "registry_empty",
])
def test_status_map_phase_3a_hard_fail_entries_exit_1(reason):
    assert STATUS_MAP[reason] == ("HARD_FAIL", 1)


def test_status_map_prose_partial_is_degraded_exit_2():
    assert STATUS_MAP["prose_partial"] == ("DEGRADED", 2)


def test_status_map_covers_all_phase_3a_values():
    """Phase 3a: 9 entries (None + 6 HARD_FAIL + 2 DEGRADED). Exact match required."""
    assert set(STATUS_MAP.keys()) == {
        None,
        "omx_failover", "required_field_unresolvable",
        "url_parse_failed", "render_timeout", "lint_cli_missing", "registry_empty",
        "prose_retry_exhausted", "prose_partial",
    }


# ---- update_status() derives final_status + exit_code ----

def test_update_status_pass_path():
    r = _report()
    r.update_status(None)
    assert r.degraded_reason is None
    assert r.final_status == "PASS"
    assert r.exit_code == 0


@pytest.mark.parametrize("reason,expected_status,expected_exit", [
    ("omx_failover",                 "HARD_FAIL", 1),
    ("required_field_unresolvable",  "HARD_FAIL", 1),
    ("url_parse_failed",             "HARD_FAIL", 1),
    ("render_timeout",               "HARD_FAIL", 1),
    ("lint_cli_missing",             "HARD_FAIL", 1),
    ("registry_empty",               "HARD_FAIL", 1),
    ("prose_retry_exhausted",        "DEGRADED",  2),
    ("prose_partial",                "DEGRADED",  2),
])
def test_update_status_parametric(reason, expected_status, expected_exit):
    """Parametric: each degraded_reason maps to exact (final_status, exit_code) integer pair."""
    r = _report()
    r.update_status(reason)
    assert r.final_status == expected_status, f"{reason}: status mismatch"
    assert r.exit_code == expected_exit, f"{reason}: exit code mismatch"


# ---- Serialization ----

def test_to_json_includes_all_required_fields():
    r = _report()
    data = json.loads(r.to_json())
    for k in ("url", "extracted_at", "registry_size", "llm_model",
              "findings_total", "schema_findings", "prose_findings",
              "fixer_actions", "retry_rounds", "final_status",
              "degraded_reason", "exit_code"):
        assert k in data


def test_to_json_serializes_fixer_actions():
    r = _report()
    r.fixer_actions.append(FixerAction(rule="broken-ref", action="nearest",
                                       target="components.button-primary.backgroundColor"))
    data = json.loads(r.to_json())
    assert len(data["fixer_actions"]) == 1
    assert data["fixer_actions"][0]["rule"] == "broken-ref"
    assert data["fixer_actions"][0]["action"] == "nearest"


def test_write_to_disk(tmp_path):
    r = _report()
    out = tmp_path / "run_report.json"
    r.write(str(out))
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["url"] == "https://x.com"


# ---- Default values ----

def test_default_final_status_is_pass():
    r = _report()
    assert r.final_status == "PASS"
    assert r.degraded_reason is None
    assert r.exit_code == 0
