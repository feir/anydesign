"""Tests for component.py — Phase 2.4 component identification.

Coverage:
- select_top_candidates heuristic determinism (chromatic-first, area-sorted)
- Monochrome fallback (plan-review M5): 0 colored → fall back to area-only
- crop_button uses PIL.Image.crop (not BrowserSession.crop_bbox per M1)
- pick_button_primary calls llm.generate with image + correct prompt
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PIL import Image

from design_from_url.component import (
    ButtonCandidate,
    crop_button,
    format_candidates_block,
    pick_button_primary,
    select_top_candidates,
)


def _btn(*, color="rgb(99, 91, 255)", classification="colored", area=10000.0,
         x=100.0, y=100.0, w=100.0, h=40.0, visible=True, text="Sign up"):
    return {
        "background_color": color,
        "classification": classification,
        "area": area,
        "rect": {"x": x, "y": y, "width": w, "height": h, "visible": visible},
        "text": text,
    }


# ---- select_top_candidates: ranking ----

def test_select_top_candidates_returns_only_colored_when_any_exist():
    """When colored candidates exist, neutrals are excluded entirely (not just
    ranked lower). Monochrome fallback only triggers when zero colored exist."""
    payload = {"button_backgrounds": [
        _btn(classification="neutral", area=20000, text="neutral-big"),
        _btn(classification="colored", area=5000, text="colored-small"),
    ]}
    out = select_top_candidates(payload)
    assert len(out) == 1
    assert out[0].text == "colored-small"
    assert out[0].classification == "colored"


def test_select_top_candidates_sorts_colored_by_area():
    payload = {"button_backgrounds": [
        _btn(classification="colored", area=1000, text="small"),
        _btn(classification="colored", area=5000, text="big"),
        _btn(classification="colored", area=2500, text="med"),
    ]}
    out = select_top_candidates(payload)
    assert [c.text for c in out] == ["big", "med", "small"]


def test_select_top_candidates_caps_at_max():
    payload = {"button_backgrounds": [
        _btn(area=a, text=f"b{a}") for a in [1000, 2000, 3000, 4000, 5000]
    ]}
    out = select_top_candidates(payload, max_candidates=2)
    assert len(out) == 2
    assert [c.text for c in out] == ["b5000", "b4000"]


def test_select_top_candidates_includes_in_viewport_excludes_below_fold():
    """Buttons within viewport (y < viewport_height) included; below-fold excluded.
    Cutoff was relaxed from y < height*0.25 to y < height after smoke testing
    showed real-site colored CTAs cluster well below the top quarter."""
    payload = {"button_backgrounds": [
        _btn(y=50, text="hero", area=10000, classification="colored"),       # in viewport
        _btn(y=500, text="midpage", area=20000, classification="colored"),   # in viewport (was excluded before)
        _btn(y=1500, text="below-fold", area=30000, classification="colored"),  # below 900 cutoff
    ]}
    out = select_top_candidates(payload)
    assert {c.text for c in out} == {"hero", "midpage"}, (
        f"got {[c.text for c in out]} — expected hero+midpage, below-fold excluded"
    )


def test_select_top_candidates_drops_invisible_and_parse_failed():
    payload = {"button_backgrounds": [
        _btn(visible=False, text="hidden"),
        _btn(classification="parse-failed", text="parse-fail"),
        _btn(classification="transparent", text="transp"),
        _btn(text="kept"),
    ]}
    out = select_top_candidates(payload)
    assert [c.text for c in out] == ["kept"]


# ---- Monochrome fallback (plan-review M5) ----

def test_monochrome_fallback_returns_neutrals_when_no_colored():
    """Vercel-case: 0 colored buttons → fall back to top-3 by area, even neutrals."""
    payload = {"button_backgrounds": [
        _btn(classification="neutral", area=15000, text="big-black"),
        _btn(classification="neutral", area=8000, text="med-black"),
        _btn(classification="neutral", area=3000, text="small-gray"),
    ]}
    out = select_top_candidates(payload)
    assert len(out) == 3
    # Ranked by area DESC even though all neutral
    assert [c.text for c in out] == ["big-black", "med-black", "small-gray"]


def test_no_buttons_returns_empty():
    out = select_top_candidates({"button_backgrounds": []})
    assert out == []


def test_no_button_backgrounds_key_returns_empty():
    out = select_top_candidates({})
    assert out == []


# ---- crop_button: uses PIL (per plan-review M1) ----

def test_crop_button_uses_pil_image_crop(tmp_path):
    """Regression guard for M1: must crop via PIL, NOT renderer.crop_bbox()."""
    # Create a 1440x900 test PNG
    src = tmp_path / "viewport.png"
    Image.new("RGB", (1440, 900), color="white").save(src)
    out = tmp_path / "crop.png"
    crop_button(str(src), {"x": 100, "y": 100, "width": 200, "height": 50},
                output_path=str(out))
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == (200, 50)


def test_crop_button_with_pad(tmp_path):
    src = tmp_path / "viewport.png"
    Image.new("RGB", (1440, 900), color="red").save(src)
    out = tmp_path / "crop.png"
    crop_button(str(src), {"x": 100, "y": 100, "width": 200, "height": 50},
                output_path=str(out), pad=10)
    with Image.open(out) as im:
        # 200+20 wide, 50+20 tall
        assert im.size == (220, 70)


def test_crop_button_clips_at_image_bounds(tmp_path):
    """Rect that extends past image bounds is clipped, not raised."""
    src = tmp_path / "viewport.png"
    Image.new("RGB", (1440, 900), color="white").save(src)
    out = tmp_path / "crop.png"
    crop_button(str(src),
                {"x": 1400, "y": 880, "width": 200, "height": 100},  # extends past 1440x900
                output_path=str(out))
    with Image.open(out) as im:
        # Clipped to image bounds
        assert im.size == (40, 20)


def test_crop_button_raises_on_missing_viewport(tmp_path):
    with pytest.raises(FileNotFoundError):
        crop_button(str(tmp_path / "nonexistent.png"),
                    {"x": 0, "y": 0, "width": 10, "height": 10},
                    output_path=str(tmp_path / "x.png"))


def test_crop_button_raises_on_empty_rect(tmp_path):
    src = tmp_path / "viewport.png"
    Image.new("RGB", (1440, 900), color="white").save(src)
    with pytest.raises(ValueError, match="empty crop"):
        crop_button(str(src),
                    {"x": 100, "y": 100, "width": 0, "height": 0},
                    output_path=str(tmp_path / "x.png"))


# ---- format_candidates_block ----

def test_format_candidates_block_contains_all_fields():
    cands = [
        ButtonCandidate(0, "rgb(99,91,255)", "colored", 4000.0,
                        {"x": 100, "y": 50, "width": 100, "height": 40}, "Sign up"),
    ]
    block = format_candidates_block(cands, ["/tmp/c0.png"])
    assert "Candidate 0" in block
    assert "Sign up" in block
    assert "rgb(99,91,255)" in block
    assert "/tmp/c0.png" in block


def test_format_candidates_block_handles_empty():
    block = format_candidates_block([], [])
    assert "no button candidates" in block.lower()


# ---- pick_button_primary ----

def test_pick_button_primary_calls_llm_with_image_and_local_model():
    cands = [
        ButtonCandidate(0, "rgb(99,91,255)", "colored", 4000.0,
                        {"x": 100, "y": 50, "width": 100, "height": 40}, "Sign up"),
    ]
    fake_llm = MagicMock(return_value=(
        'button-primary:\n'
        '  backgroundColor: "{colors.primary}"\n'
        '  color: "{colors.neutral_light}"'
    ))
    result = pick_button_primary(
        cands, ["/tmp/c0.png"], registry_yaml="primary: '#635bff'",
        llm_generate=fake_llm,
    )
    fake_llm.assert_called_once()
    kwargs = fake_llm.call_args.kwargs
    # Vision call: image_path and local model
    assert kwargs["image_path"] == "/tmp/c0.png"
    assert kwargs["model"].startswith("local/")
    # Result preserves LLM output
    assert "button-primary" in result
    assert '{colors.primary}' in result


def test_pick_button_primary_raises_on_empty_candidates():
    with pytest.raises(ValueError, match="no button candidates"):
        pick_button_primary([], [], registry_yaml="x", llm_generate=lambda *a, **k: "")


def test_pick_button_primary_raises_on_empty_crops():
    cands = [ButtonCandidate(0, "rgb(0,0,0)", "colored", 1.0, {}, "x")]
    with pytest.raises(ValueError, match="no button crops"):
        pick_button_primary(cands, [], registry_yaml="x",
                            llm_generate=lambda *a, **k: "")
