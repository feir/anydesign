"""Unit tests for the numeric clustering aggregator (Phase 1.5a)."""

from __future__ import annotations

import json
from pathlib import Path

from design_from_url.aggregator import (
    aggregate_spacing_and_rounded,
    cluster_lengths,
    parse_lengths,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _approx_eq(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _approx_list(actual: list[float], expected: list[float], tol: float = 1.0) -> bool:
    if len(actual) != len(expected):
        return False
    return all(_approx_eq(a, e, tol) for a, e in zip(actual, expected))


# ---- Synthetic algorithm tests ----

def test_cluster_unique_count_below_k_returns_each_value():
    clusters = cluster_lengths([4.0, 8.0, 12.0], k_max=5)
    reps = sorted(c.representative_px for c in clusters)
    assert reps == [4.0, 8.0, 12.0]
    assert all(c.frequency == 1 for c in clusters)


def test_cluster_separates_distinct_modes():
    # Three obvious clusters around 4, 16, 48; weights chosen so 16 is dominant.
    values = [4.0] * 5 + [16.0] * 12 + [48.0] * 7
    clusters = cluster_lengths(values, k_max=3)
    reps = sorted(c.representative_px for c in clusters)
    assert _approx_list(reps, [4.0, 16.0, 48.0], tol=0.5), reps
    assert clusters[0].representative_px == 16.0
    assert clusters[0].frequency == 12


def test_cluster_merges_close_values_within_tolerance():
    # 16 and 17 are perceptually one bucket; k_max=2 should merge them.
    values = [4.0] * 4 + [16.0, 16.0, 17.0, 17.0, 16.5]
    clusters = cluster_lengths(values, k_max=2)
    reps = sorted(c.representative_px for c in clusters)
    assert _approx_eq(reps[0], 4.0, tol=0.5), reps
    assert _approx_eq(reps[1], 16.5, tol=1.0), reps


def test_cluster_k_max_caps_output():
    values = [float(x) for x in range(10)]
    clusters = cluster_lengths(values, k_max=3)
    assert len(clusters) <= 3
    # Sum of cluster frequencies must equal input length.
    assert sum(c.frequency for c in clusters) == len(values)


def test_cluster_empty_input_returns_empty():
    assert cluster_lengths([], k_max=5) == []


# ---- Parse helpers ----

def test_parse_lengths_prefers_histogram_over_computed_styles():
    payload = {
        "length_histogram": {"padding": [4.0, 4.0, 8.0, 8.0, 8.0, 16.0]},
        "computed_styles": [
            {"selector": "h1", "sample_index": 0, "padding": "100px"},
        ],
    }
    samples = parse_lengths(payload, fields=("padding",))
    assert sorted(s.value_px for s in samples) == [4.0, 4.0, 8.0, 8.0, 8.0, 16.0]
    # Verify provenance: histogram path uses "*" sentinel.
    assert all(s.selector == "*" for s in samples)


def test_parse_lengths_falls_back_to_computed_styles_when_histogram_missing():
    payload = {
        "computed_styles": [
            {"selector": "button", "sample_index": 0, "padding": "12px 24px"},
            {"selector": "a", "sample_index": 0, "padding": "8px"},
        ],
    }
    samples = parse_lengths(payload, fields=("padding",))
    assert sorted(s.value_px for s in samples) == [8.0, 12.0, 24.0]


def test_parse_lengths_drops_zero_by_default():
    payload = {"length_histogram": {"padding": [0.0, 0.0, 16.0]}}
    samples = parse_lengths(payload, fields=("padding",))
    assert [s.value_px for s in samples] == [16.0]


# ---- Fixture-based tests (real Tailwind extraction) ----

def _load_fixture(name: str) -> dict:
    path = FIXTURES / name
    return json.loads(path.read_text())


def test_aggregate_tailwind_spacing_includes_canonical_buckets():
    """Tailwind v4 (live site at fixture-capture time) shows top spacing
    buckets {4, 8, 12, 16, 32}. We assert the canonical {4, 8, 16} are
    present within ±2px — these are stable across Tailwind versions even
    when the high-end (24/32/48) shifts.
    """
    payload = _load_fixture("tailwind_extract.json")
    agg = aggregate_spacing_and_rounded(payload)
    reps = [c["representative_px"] for c in agg["spacing"]]
    canonical = [4.0, 8.0, 16.0]
    for c in canonical:
        assert any(abs(r - c) <= 2 for r in reps), (
            f"canonical Tailwind spacing {c}px not in clusters {reps}"
        )


def test_aggregate_tailwind_rounded_caps_at_5_clusters():
    payload = _load_fixture("tailwind_extract.json")
    agg = aggregate_spacing_and_rounded(payload, k_max=5)
    assert len(agg["rounded"]) <= 5
    # Each cluster must have at least one member.
    assert all(c["frequency"] >= 1 for c in agg["rounded"])


def test_aggregate_tailwind_clusters_are_frequency_ranked():
    payload = _load_fixture("tailwind_extract.json")
    agg = aggregate_spacing_and_rounded(payload)
    spacing_freqs = [c["frequency"] for c in agg["spacing"]]
    assert spacing_freqs == sorted(spacing_freqs, reverse=True)
