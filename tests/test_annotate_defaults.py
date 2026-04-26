"""Unit tests for `_annotate_defaults` — runs without Playwright."""

from __future__ import annotations

from design_from_url.extractor import _annotate_defaults


def _sample(**overrides):
    base = {
        "selector": "h1",
        "sample_index": 0,
        "color": "rgb(34, 34, 34)",
        "background-color": "rgb(255, 255, 255)",
        "font-family": "Inter, sans-serif",
        "font-size": "32px",
        "font-weight": "700",
        "line-height": "40px",
        "letter-spacing": "0px",
        "border-radius": "0px",
        "padding": "0px",
        "rect": {"x": 0, "y": 0, "width": 100, "height": 40, "visible": True},
    }
    base.update(overrides)
    return base


def test_distinct_non_default_counts_real_sample():
    payload = {
        "computed_styles": [_sample()],
        "button_backgrounds": [],
        "root_vars": {},
    }
    annotated = _annotate_defaults(payload)
    assert annotated["_meta"]["computed_distinct_non_default"] == 1
    assert annotated["computed_styles"][0]["_is_default_color"] is False
    assert annotated["computed_styles"][0]["_is_default_family"] is False


def test_browser_default_sample_is_flagged():
    payload = {
        "computed_styles": [_sample(
            color="rgb(0, 0, 0)",
            **{"background-color": "rgba(0, 0, 0, 0)"},
            **{"font-family": "Times"},
        )],
        "button_backgrounds": [],
        "root_vars": {},
    }
    annotated = _annotate_defaults(payload)
    assert annotated["_meta"]["computed_distinct_non_default"] == 0
    s = annotated["computed_styles"][0]
    assert s["_is_default_color"] is True
    assert s["_is_default_bg"] is True
    assert s["_is_default_family"] is True


def test_meta_counts_root_vars_and_buttons():
    payload = {
        "computed_styles": [_sample()],
        "button_backgrounds": [
            {"classification": "colored", "background_color": "rgb(99, 91, 255)"},
            {"classification": "neutral", "background_color": "rgb(240, 240, 240)"},
            {"classification": "transparent", "background_color": "rgba(0, 0, 0, 0)"},
        ],
        "root_vars": {"--color-primary": "#635bff", "--radius-md": "8px"},
    }
    annotated = _annotate_defaults(payload)
    meta = annotated["_meta"]
    assert meta["root_vars_count"] == 2
    assert meta["buttons_total"] == 3
    assert meta["buttons_colored"] == 1


def test_meta_handles_partially_default_sample():
    # Default color but custom font-family: still counts as non-default sample.
    payload = {
        "computed_styles": [_sample(color="rgb(0, 0, 0)")],
        "button_backgrounds": [],
        "root_vars": {},
    }
    annotated = _annotate_defaults(payload)
    assert annotated["_meta"]["computed_distinct_non_default"] == 1
