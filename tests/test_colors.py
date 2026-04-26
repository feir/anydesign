"""Unit tests for color parsing, sRGB↔Lab, and ΔE76 dedupe (Phase 1.6)."""

from __future__ import annotations

import json
from pathlib import Path

from design_from_url.colors import (
    ColorCluster,
    RGBA,
    collect_color_strings,
    dedupe_colors,
    delta_e76,
    parse_color,
    srgb_to_lab,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _approx_eq(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol


# ---- parse_color ----

def test_parse_color_hex_6():
    rgba = parse_color("#635bff")
    assert rgba == RGBA(r=99, g=91, b=255, a=1.0)


def test_parse_color_hex_3_expands():
    rgba = parse_color("#fb0")
    assert rgba == RGBA(r=255, g=187, b=0, a=1.0)


def test_parse_color_hex_8_carries_alpha():
    rgba = parse_color("#635bff80")
    assert rgba.r == 99 and rgba.g == 91 and rgba.b == 255
    assert _approx_eq(rgba.a, 128 / 255, tol=1e-3)


def test_parse_color_rgb():
    assert parse_color("rgb(99, 91, 255)") == RGBA(r=99, g=91, b=255, a=1.0)


def test_parse_color_rgba_with_alpha():
    rgba = parse_color("rgba(99, 91, 255, 0.5)")
    assert rgba.r == 99 and rgba.g == 91 and rgba.b == 255
    assert rgba.a == 0.5


def test_parse_color_drops_neutral_keywords():
    assert parse_color("transparent") is None
    assert parse_color("currentcolor") is None
    assert parse_color("inherit") is None


def test_parse_color_named_basic_colors():
    assert parse_color("black") == RGBA(r=0, g=0, b=0, a=1.0)
    assert parse_color("white") == RGBA(r=255, g=255, b=255, a=1.0)
    assert parse_color("red") == RGBA(r=255, g=0, b=0, a=1.0)


def test_parse_color_lab_round_trip_to_red():
    # Pure red lab(53.24, 80.09, 67.20) should round-trip to (255, 0, 0)
    # within 1 LSB tolerance.
    rgba = parse_color("lab(53.24 80.09 67.20)")
    assert rgba is not None
    assert abs(rgba.r - 255) <= 1
    assert abs(rgba.g - 0) <= 1
    assert abs(rgba.b - 0) <= 1


def test_parse_color_oklab_known_blue():
    # Stripe brand #635bff in OKLab is approximately oklab(0.55 0.10 -0.27).
    rgba = parse_color("oklab(0.55 0.10 -0.27)")
    assert rgba is not None
    # Channel-level identity is impossible due to clipping; expect blueish.
    assert rgba.b > rgba.r and rgba.b > rgba.g


def test_parse_color_oklch_round_trip_oklab():
    # oklch(0.55 0.288 290) ≈ oklab(0.55 0.10 -0.27) via polar->rect.
    a = parse_color("oklch(0.55 0.288 290)")
    b = parse_color("oklab(0.55 0.0985 -0.2706)")
    assert a is not None and b is not None
    # Conversion paths must agree within 1 sRGB step.
    assert abs(a.r - b.r) <= 2
    assert abs(a.g - b.g) <= 2
    assert abs(a.b - b.b) <= 2


def test_parse_color_lab_with_percent_L():
    rgba = parse_color("lab(50% 0 0)")
    # Percent in lab() is same numeric scale as 0..100 (gray).
    assert rgba is not None
    # Mid-gray expected — channels should be near-equal.
    assert abs(rgba.r - rgba.g) <= 2
    assert abs(rgba.g - rgba.b) <= 2


# ---- sRGB → Lab + ΔE76 ----

def test_srgb_to_lab_known_values():
    # White (255, 255, 255) -> L*=100, a*=0, b*=0
    L, a, b = srgb_to_lab(RGBA(r=255, g=255, b=255, a=1.0))
    assert _approx_eq(L, 100.0, tol=0.5)
    assert _approx_eq(a, 0.0, tol=0.5)
    assert _approx_eq(b, 0.0, tol=0.5)
    # Pure red (255, 0, 0) -> known L*≈53.24, a*≈80.09, b*≈67.20
    L, a, b = srgb_to_lab(RGBA(r=255, g=0, b=0, a=1.0))
    assert _approx_eq(L, 53.24, tol=0.5)
    assert _approx_eq(a, 80.09, tol=0.5)
    assert _approx_eq(b, 67.20, tol=0.5)


def test_delta_e76_identity_is_zero():
    lab = srgb_to_lab(RGBA(r=99, g=91, b=255, a=1.0))
    assert delta_e76(lab, lab) == 0.0


def test_delta_e76_close_colors_below_threshold():
    # #1A1C1E vs #1A1D1E from plan 1.6 example — should be < 6.
    a = srgb_to_lab(RGBA(r=0x1A, g=0x1C, b=0x1E, a=1.0))
    b = srgb_to_lab(RGBA(r=0x1A, g=0x1D, b=0x1E, a=1.0))
    assert delta_e76(a, b) < 6.0


def test_delta_e76_distinct_colors_above_threshold():
    a = srgb_to_lab(RGBA(r=255, g=0, b=0, a=1.0))   # red
    b = srgb_to_lab(RGBA(r=0, g=0, b=255, a=1.0))   # blue
    assert delta_e76(a, b) > 100


# ---- dedupe_colors ----

def test_dedupe_merges_close_colors():
    inputs = ["#1a1c1e", "#1a1d1e", "#1a1c1e", "#ff0000"]
    clusters = dedupe_colors(inputs)
    # Two clusters: dark gray (frequency 3) + red (frequency 1).
    assert len(clusters) == 2
    assert clusters[0].frequency == 3
    assert clusters[0].representative == "#1a1c1e"  # most-frequent member
    assert clusters[1].representative == "#ff0000"


def test_dedupe_orders_by_frequency_desc():
    inputs = (
        ["#ff0000"] * 10
        + ["#00ff00"] * 5
        + ["#0000ff"] * 7
    )
    clusters = dedupe_colors(inputs)
    freqs = [c.frequency for c in clusters]
    assert freqs == sorted(freqs, reverse=True)
    assert clusters[0].frequency == 10


def test_dedupe_drops_low_alpha_colors():
    inputs = ["rgba(255, 0, 0, 0.1)", "rgba(255, 0, 0, 0.9)", "#ff0000"]
    clusters = dedupe_colors(inputs)
    # The 0.1 alpha entry is dropped; remaining 2 are exact match → 1 cluster.
    assert len(clusters) == 1
    assert clusters[0].frequency == 2


def test_dedupe_normalizes_modern_color_spaces_to_rgb():
    # lab/oklab inputs convert to sRGB and join the dedupe pool. Here all
    # three inputs are perceptually red and should fold into one cluster.
    inputs = ["#ff0000", "lab(53.24 80.09 67.20)", "oklab(0.628 0.225 0.126)"]
    clusters = dedupe_colors(inputs)
    assert len(clusters) == 1
    assert clusters[0].frequency == 3


def test_dedupe_empty_input_returns_empty():
    assert dedupe_colors([]) == []


def test_dedupe_threshold_respected():
    # #635bff and #5e58fa are perceptually close — should merge at 6.
    inputs = ["#635bff", "#5e58fa", "#635bff"]
    clusters = dedupe_colors(inputs, delta_e_threshold=6.0)
    assert len(clusters) == 1
    # With a tight threshold (1.0) they separate.
    clusters = dedupe_colors(inputs, delta_e_threshold=1.0)
    assert len(clusters) == 2


# ---- collect_color_strings ----

def test_collect_color_strings_pulls_from_all_sources():
    payload = {
        "root_vars": {"--brand": "#635bff", "--radius": "8px"},
        "computed_styles": [
            {"color": "rgb(0, 0, 0)", "background-color": "rgba(255, 255, 255, 1)"},
        ],
        "button_backgrounds": [
            {"background_color": "#e8e9ff"},
        ],
    }
    out = collect_color_strings(payload)
    # Order: root_vars first, then computed_styles, then buttons.
    assert "#635bff" in out
    assert "rgb(0, 0, 0)" in out
    assert "#e8e9ff" in out
    # Non-color root_vars values are passed through (filtered later in dedupe).
    assert "8px" in out


# ---- Fixture-based test ----

def test_dedupe_tailwind_extracts_real_brand():
    payload = json.loads((FIXTURES / "tailwind_extract.json").read_text())
    raw = collect_color_strings(payload)
    clusters = dedupe_colors(raw)
    # Tailwind site has at least a handful of distinct brand-ish colors;
    # frequency-ranked top 5 should not all be black/white.
    assert len(clusters) >= 5
    top5 = [c.representative for c in clusters[:5]]
    non_neutral = [
        h for h in top5
        if h not in {"#000000", "#ffffff", "#fefefe"}
    ]
    assert len(non_neutral) >= 1, f"top5 unexpectedly all neutral: {top5}"
