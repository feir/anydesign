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


def test_status_map_omx_failover_is_hard_fail():
    assert STATUS_MAP["omx_failover"] == ("HARD_FAIL", 2)


def test_status_map_required_field_unresolvable_is_hard_fail():
    assert STATUS_MAP["required_field_unresolvable"] == ("HARD_FAIL", 2)


def test_status_map_prose_retry_exhausted_is_degraded():
    assert STATUS_MAP["prose_retry_exhausted"] == ("DEGRADED", 2)


def test_status_map_covers_all_4_documented_values():
    """Defensive: D6.1 table has 4 rows; STATUS_MAP must match exactly."""
    assert set(STATUS_MAP.keys()) == {
        None, "omx_failover", "required_field_unresolvable", "prose_retry_exhausted",
    }


# ---- update_status() derives final_status + exit_code ----

def test_update_status_pass_path():
    r = _report()
    r.update_status(None)
    assert r.degraded_reason is None
    assert r.final_status == "PASS"
    assert r.exit_code == 0


def test_update_status_hard_fail_path():
    r = _report()
    r.update_status("omx_failover")
    assert r.final_status == "HARD_FAIL"
    assert r.exit_code == 2


def test_update_status_degraded_path():
    r = _report()
    r.update_status("prose_retry_exhausted")
    assert r.final_status == "DEGRADED"
    assert r.exit_code == 2


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
