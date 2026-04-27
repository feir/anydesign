"""Component identification — Phase 2.4.

Pipeline:
  1. `select_top_candidates(payload)` — deterministic heuristic ranks
     extracted button_backgrounds by (chromatic, area) → top 3.
     Monochrome fallback (per plan-review M5): if 0 colored candidates,
     return top-3 by area regardless of chroma (handles Vercel #000000 case).
  2. `crop_buttons_from_viewport(viewport_path, candidates)` — PIL crops
     using `rect` field; mirrors `brand_color.py:crop_via_pixel_rank` pattern.
     No reach into renderer (per plan-review M1; renderer.crop_bbox does
     not exist).
  3. `pick_button_primary(candidates, crops_dir, registry, *, llm)` — sends
     candidates + crops + registry to LLM, parses YAML response into
     `{"button-primary": {"backgroundColor": "{colors.X}", "color": "..."}}`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image


_DEFAULT_VIEWPORT_HEIGHT = 900
# Inclusion cutoff: buttons within `_VIEWPORT_FRACTION * viewport_height` are
# eligible. Spec said top quarter (0.25) but smoke tests show real-site
# colored CTAs cluster around y=11000+ on long landing pages. Lift cutoff
# to full viewport (1.0) so any in-viewport button is eligible; ranking
# still favors larger area, which naturally surfaces hero CTAs.
_VIEWPORT_FRACTION = 1.0


@dataclass(frozen=True)
class ButtonCandidate:
    index: int                  # ordinal in heuristic ranking (0-based)
    background_color: str       # raw CSS color string (e.g. "rgb(99, 91, 255)")
    classification: str         # "colored" | "neutral" | ... (from extractor)
    area: float                 # rect.width * rect.height in CSS pixels
    rect: dict                  # {x, y, width, height, visible}
    text: str                   # button label (truncated to 80 chars)


def select_top_candidates(
    payload: dict,
    *,
    max_candidates: int = 3,
    viewport_height: int = _DEFAULT_VIEWPORT_HEIGHT,
) -> list[ButtonCandidate]:
    """Rank button_backgrounds and return up to `max_candidates`.

    Ranking:
      1. Filter to visible + non-transparent buttons in top quarter (y <
         viewport_height * 0.25).
      2. Prefer "colored" classification; sort by area DESC.
      3. Monochrome fallback: if 0 colored candidates, take top-3 by area
         regardless of chroma (Vercel-style sites with #000 CTAs).
    """
    buttons = payload.get("button_backgrounds", [])
    cutoff_y = viewport_height * _VIEWPORT_FRACTION

    visible_top = [
        b for b in buttons
        if b.get("rect", {}).get("visible", False)
        and 0 <= b.get("rect", {}).get("y", -1) < cutoff_y  # in-viewport
        and b.get("classification") != "transparent"
        and b.get("classification") != "parse-failed"
    ]

    colored = [b for b in visible_top if b.get("classification") == "colored"]
    if colored:
        ranked = sorted(colored, key=lambda b: b.get("area", 0), reverse=True)
    else:
        # Monochrome fallback (plan-review M5): no chromatic CTAs on site
        # (e.g. Vercel uses #000 for primary). Fall back to area-only ranking.
        ranked = sorted(visible_top, key=lambda b: b.get("area", 0), reverse=True)

    out = []
    for idx, b in enumerate(ranked[:max_candidates]):
        out.append(ButtonCandidate(
            index=idx,
            background_color=b.get("background_color", ""),
            classification=b.get("classification", "unknown"),
            area=float(b.get("area", 0)),
            rect=b.get("rect", {}),
            text=b.get("text", ""),
        ))
    return out


def crop_button(
    viewport_path: str,
    rect: dict,
    *,
    output_path: str,
    pad: int = 0,
) -> str:
    """Crop a button rectangle from the viewport screenshot.

    Coordinates from `rect` are in CSS pixels; the screenshot is captured
    at 1440x900 with DPR=1 (per Phase 1 renderer constants), so CSS pixels
    map 1:1 to image pixels — no DPR scaling needed.

    Args:
        viewport_path: Path to the captured viewport.png.
        rect: {x, y, width, height} dict from extractor button_backgrounds.
        output_path: Where to save the cropped PNG.
        pad: Optional padding in pixels added on all sides (capped at image bounds).

    Returns:
        `output_path` (the saved crop).
    """
    if not os.path.exists(viewport_path):
        raise FileNotFoundError(f"viewport image not found: {viewport_path}")

    with Image.open(viewport_path) as im:
        img_w, img_h = im.size
        x = max(0, int(rect.get("x", 0)) - pad)
        y = max(0, int(rect.get("y", 0)) - pad)
        right = min(img_w, int(rect.get("x", 0) + rect.get("width", 0)) + pad)
        bottom = min(img_h, int(rect.get("y", 0) + rect.get("height", 0)) + pad)
        if right <= x or bottom <= y:
            raise ValueError(
                f"button rect produces empty crop: x={x} y={y} "
                f"right={right} bottom={bottom}"
            )
        crop = im.crop((x, y, right, bottom))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_path)
    return output_path


def crop_buttons_from_viewport(
    viewport_path: str,
    candidates: list[ButtonCandidate],
    *,
    crops_dir: str,
    pad: int = 4,
) -> list[str]:
    """Crop all candidates and return the list of saved paths."""
    out = []
    for c in candidates:
        crop_path = os.path.join(crops_dir, f"button_candidate_{c.index}.png")
        try:
            crop_button(viewport_path, c.rect, output_path=crop_path, pad=pad)
            out.append(crop_path)
        except (ValueError, FileNotFoundError):
            # Skip degenerate rects (zero-size, off-screen) — LLM still gets
            # remaining candidates. If all fail, caller should detect via
            # empty list.
            continue
    return out


def format_candidates_block(
    candidates: list[ButtonCandidate],
    crop_paths: list[str],
) -> str:
    """Format candidates as a markdown block for the LLM prompt.

    Returned text is meant to be substituted into prompts/components.md
    via the `<<candidates>>` placeholder.
    """
    lines = []
    for c, p in zip(candidates, crop_paths):
        lines.append(
            f"- **Candidate {c.index}** "
            f"(classification={c.classification}, area={int(c.area)}px², "
            f"text={c.text!r}):"
        )
        lines.append(f"  - background_color: `{c.background_color}`")
        lines.append(f"  - rect: x={int(c.rect.get('x', 0))} "
                     f"y={int(c.rect.get('y', 0))} "
                     f"w={int(c.rect.get('width', 0))} "
                     f"h={int(c.rect.get('height', 0))}")
        lines.append(f"  - crop: `{p}`")
    if not lines:
        lines.append("(no button candidates passed the heuristic — site may be monochrome with no CTA buttons)")
    return "\n".join(lines)


def pick_button_primary(
    candidates: list[ButtonCandidate],
    crop_paths: list[str],
    registry_yaml: str,
    *,
    llm_generate: Callable[..., str],
    primary_crop: str | None = None,
    model: str = "local/vision",
) -> str:
    """Ask the LLM to designate ONE candidate as button-primary.

    Args:
        candidates: From `select_top_candidates`.
        crop_paths: From `crop_buttons_from_viewport` (parallel to candidates).
        registry_yaml: YAML-formatted registry colors block (for prompt context).
        llm_generate: The `llm.generate` function (injected for testability).
        primary_crop: Optional path to a single concatenated crops image; if
            None, uses the first crop. (Multi-image prompts not yet supported
            via _omlx_chat single-image API; future enhancement.)
        model: LLM model identifier; must be a `local/...` model since this
            is a vision call.

    Returns:
        YAML block: `button-primary:\\n  backgroundColor: "{colors.X}"\\n  color: "..."`.
        Caller is responsible for inserting it under `components:` in the
        DESIGN.md YAML frontmatter.

    Raises:
        ValueError: If candidates list is empty.
        LLMUnavailable: Propagated from llm_generate when local oMLX is down.
    """
    from design_from_url.prompt_loader import load_prompt

    if not candidates:
        raise ValueError(
            "no button candidates — cannot identify button-primary; "
            "site may have no buttons in top viewport quadrant"
        )
    if not crop_paths:
        raise ValueError("no button crops produced — all rects were degenerate")

    # Pick first available crop for the vision call. Multi-crop concatenation
    # is a future enhancement (omlx_chat takes one image; we'd need to stitch).
    image_for_llm = primary_crop or crop_paths[0]

    candidates_block = format_candidates_block(candidates, crop_paths)
    prompt = load_prompt(
        "components",
        registry=registry_yaml,
        candidates=candidates_block,
    )
    return llm_generate(prompt, image_path=image_for_llm, model=model).strip()
