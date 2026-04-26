"""Contract test for `_extract_with_session` helper (Phase 3a 3a.5a).

Defends against silent contract drift between `_extract_with_session`
(used by Phase 3a dark-mode dual-run) and the public `extract_from_url`
wrapper (used by the main pipeline). They MUST share the same output
shape — light/dark dual-run depends on it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from design_from_url.extractor import _extract_with_session


def _fake_eval_payload() -> dict:
    """Minimum-viable payload mirroring the structure the JS pass returns."""
    return {
        "root_vars": {"--c": "red"},
        "computed_styles": [
            {
                "selector": "h1", "sample_index": 0,
                "color": "rgb(0, 0, 0)",
                "background-color": "rgba(0, 0, 0, 0)",
                "font-family": "Helvetica, sans-serif",
                "font-size": "32px",
                "font-weight": "700",
                "line-height": "40px",
                "letter-spacing": "0px",
                "border-radius": "0px",
                "padding": "0px",
                "rect": {"x": 0, "y": 0, "width": 100, "height": 40, "visible": True},
            },
        ],
        "button_backgrounds": [],
        "length_histogram": {"padding": [], "border-radius": [], "gap": []},
        "length_histogram_meta": {"elements_scanned": 0, "fields": ["padding"]},
        "viewport": {"width": 1440, "height": 900, "dpr": 1},
    }


def _fake_session(eval_return=None):
    s = MagicMock()
    s.eval_js.return_value = eval_return if eval_return is not None else _fake_eval_payload()
    return s


def _fake_info():
    return SimpleNamespace(
        final_url="https://example.com/landed",
        page_title="Example",
        html_size=12345,
    )


def test_helper_stamps_render_info_onto_payload():
    session = _fake_session()
    info = _fake_info()
    out = _extract_with_session(session, info)
    assert out["url"] == "https://example.com/landed"
    assert out["page_title"] == "Example"
    assert out["html_size"] == 12345


def test_helper_includes_annotated_defaults_meta():
    """`_annotate_defaults` must run inside the helper — its `_meta` block
    is part of the contract dual-mode aggregation depends on."""
    session = _fake_session()
    info = _fake_info()
    out = _extract_with_session(session, info)
    assert "_meta" in out
    assert "computed_distinct_non_default" in out["_meta"]
    assert "buttons_total" in out["_meta"]
    assert "buttons_colored" in out["_meta"]


def test_helper_does_not_take_screenshot_when_path_is_none():
    session = _fake_session()
    info = _fake_info()
    _extract_with_session(session, info, screenshot_path=None)
    session.screenshot.assert_not_called()
    # screenshot_path key must NOT be added when no path requested
    out = _extract_with_session(session, info)
    assert "screenshot_path" not in out


def test_helper_takes_screenshot_when_path_provided(tmp_path):
    session = _fake_session()
    info = _fake_info()
    target = str(tmp_path / "viewport.png")
    out = _extract_with_session(session, info, screenshot_path=target)
    session.screenshot.assert_called_once_with(target, timeout_s=30)
    assert out["screenshot_path"] == target


def test_helper_propagates_screenshot_timeout(tmp_path):
    """screenshot_timeout_s must reach session.screenshot, not stay default.

    Regression guard for the Phase 3a hardcoded-30s bug: heavy sites
    (Stripe) need >30s viewport screenshot, and CLI --timeout was being
    silently ignored at the screenshot step.
    """
    session = _fake_session()
    info = _fake_info()
    target = str(tmp_path / "viewport.png")
    _extract_with_session(
        session, info,
        screenshot_path=target,
        screenshot_timeout_s=90,
    )
    session.screenshot.assert_called_once_with(target, timeout_s=90)


def test_helper_does_not_open_or_close_session():
    """Caller owns session lifecycle. Helper must NOT call open_url, close,
    or set_viewport — those belong to extract_from_url / extract_dual_mode."""
    session = _fake_session()
    info = _fake_info()
    _extract_with_session(session, info)
    session.close.assert_not_called()
    session.open_url.assert_not_called()
    session.set_viewport.assert_not_called()


def test_helper_calls_eval_js_exactly_once():
    """One JS pass per call. Dual-mode invokes the helper twice — once per
    color scheme — so each call must be self-contained."""
    session = _fake_session()
    info = _fake_info()
    _extract_with_session(session, info)
    assert session.eval_js.call_count == 1
