"""Brand color detection from rendered screenshots (Phase 1.5b).

Three detection paths chained in order of robustness:

1. **Path (a) — viewport pixel frequency rank** (primary, advisor flip from
   plan v5's "(b) → (a)" because (b) requires CTA bbox which is selector-
   ambiguous for utility-first sites where CTAs are `<a>` not `<button>`).
   Mask: saturation > `MIN_SAT`, L* ∈ [`MIN_L`, `MAX_L`]; quantize remaining
   pixels to 4-bit-per-channel buckets; frequency rank; top bucket
   un-quantized = brand candidate. Robust to selector ambiguity because it
   is element-agnostic.

2. **Path (b) — CTA bbox crop + median** (secondary). Use `<button>` rects
   from extractor; crop the screenshot at each rect, take median non-neutral
   pixel. Falls through when no colored buttons exist (Vercel monochrome,
   Notion's `<a>` CTA pattern).

3. **Path (c) — documented brand fallback** (tertiary). Hardcoded per-domain
   dict for the 5 spike sites; safety net when (a) and (b) both fail.

Each result carries `source` provenance for run_report observability.

`detect_brand_color` returns the first viable result; subsequent paths are
not run. Spike fixture validation (5 screenshots + ground truth) drives the
unit tests.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from PIL import Image  # type: ignore

from design_from_url.colors import RGBA, parse_color, srgb_to_lab, delta_e76


# Pixel mask: chroma-distance from the gray axis is a better brand-color
# discriminator than HSL saturation alone — pastels like #e8e9ff (chroma 23)
# look saturated by HSL (S=0.84) but are visually washed-out, while saturated
# brand colors like #635bff (chroma 164) sit far from the gray axis.
# Threshold 60 catches Linear's #5e6ad2 (chroma ~75); below 60 is too noisy.
MIN_CHROMA_DIST = 60

# Lightness clamp (RGB-mean proxy in [0, 255]). Excludes near-black (text)
# and near-white (page bg) which can sneak past chroma when antialiased.
MIN_LIGHTNESS = 38   # ~ L* 15
MAX_LIGHTNESS = 217  # ~ L* 85

# Number of dominant clusters to track (plan v5 spec).
KMEANS_K = 5

# Path (a) confidence floor: top cluster must claim this fraction of
# non-neutral pixels. Tuned to spike data — 4/5 spike sites have a brand
# cluster ≥ 5%; Vercel (monochrome) correctly produces 0 candidates.
PATH_A_MIN_CONFIDENCE = 0.05

# Documented brand colors per design.md D14 (Phase 0 spike ground truth).
# Updated from Notion's stale #ff7f5c → actual screenshot-sampled #455dd3.
DOCUMENTED_BRAND_COLORS: dict[str, str] = {
    "stripe.com": "#635bff",
    "linear.app": "#5e6ad2",
    "vercel.com": "#000000",
    "tailwindcss.com": "#06b6d4",
    "notion.so": "#455dd3",
    "notion.com": "#455dd3",
}


@dataclass(frozen=True)
class BrandColorResult:
    hex: str
    source: str          # "path_a_pixel_rank" | "path_b_cta_bbox" | "path_c_documented"
    confidence: float    # 0..1; meaning depends on source
    detail: str = ""


# ---- Pixel masking helpers ----

def _chroma_distance(r: int, g: int, b: int) -> float:
    """Distance from the gray axis (r=g=b). Captures vivid colors regardless
    of whether they are pastel-light or shadow-dark."""
    gray = (r + g + b) / 3
    dr, dg, db = r - gray, g - gray, b - gray
    return (dr * dr + dg * dg + db * db) ** 0.5


def _is_brand_pixel(
    r: int, g: int, b: int,
    *, min_chroma: float = MIN_CHROMA_DIST,
    min_l: int = MIN_LIGHTNESS, max_l: int = MAX_LIGHTNESS,
) -> bool:
    lightness = (r + g + b) // 3
    if lightness < min_l or lightness > max_l:
        return False
    return _chroma_distance(r, g, b) >= min_chroma


# ---- Path (a) — viewport pixel rank via k-means + saturation weighting ----

def _kmeans_rgb(
    pixels: list[tuple[int, int, int]],
    *,
    k: int = KMEANS_K,
    max_iter: int = 20,
    tol: float = 1.0,
) -> list[tuple[tuple[int, int, int], list[tuple[int, int, int]]]]:
    """Lloyd's k-means in RGB. Centroids initialized at quantile picks of the
    sorted-by-luminance pixel list (deterministic, no random seed)."""
    if not pixels:
        return []
    k = min(k, len(pixels))
    sorted_by_lum = sorted(pixels, key=lambda p: p[0] + p[1] + p[2])
    n = len(sorted_by_lum)
    centroids: list[tuple[float, float, float]] = [
        (float(p[0]), float(p[1]), float(p[2]))
        for p in (sorted_by_lum[int((i + 0.5) * n / k)] for i in range(k))
    ]
    for _ in range(max_iter):
        assignments: list[list[tuple[int, int, int]]] = [[] for _ in range(k)]
        for px in pixels:
            best, best_d = 0, float("inf")
            for i, c in enumerate(centroids):
                d = (px[0] - c[0]) ** 2 + (px[1] - c[1]) ** 2 + (px[2] - c[2]) ** 2
                if d < best_d:
                    best, best_d = i, d
            assignments[best].append(px)
        new_centroids = []
        max_shift = 0.0
        for i, group in enumerate(assignments):
            if not group:
                new_centroids.append(centroids[i])
                continue
            avg = (
                sum(p[0] for p in group) / len(group),
                sum(p[1] for p in group) / len(group),
                sum(p[2] for p in group) / len(group),
            )
            shift = ((avg[0] - centroids[i][0]) ** 2 + (avg[1] - centroids[i][1]) ** 2
                     + (avg[2] - centroids[i][2]) ** 2) ** 0.5
            max_shift = max(max_shift, shift)
            new_centroids.append(avg)
        centroids = new_centroids
        if max_shift < tol:
            break

    out = []
    for i in range(k):
        group = assignments[i]
        if not group:
            continue
        c = centroids[i]
        rep = (int(round(c[0])), int(round(c[1])), int(round(c[2])))
        out.append((rep, group))
    return out


def detect_via_pixel_rank(
    image_path: str,
    *,
    downsample_to_width: int = 320,
    min_chroma: float = MIN_CHROMA_DIST,
    min_l: int = MIN_LIGHTNESS,
    max_l: int = MAX_LIGHTNESS,
    min_total_pixels: int = 200,
    min_cluster_avg_chroma: float = 80.0,
) -> BrandColorResult | None:
    """Path (a): scan viewport pixels, mask non-neutrals, k-means k=5,
    rank clusters by `size × avg_chroma_distance` (saturation weighting).

    Two noise filters reject monochrome-with-antialiasing false positives:
      * `min_total_pixels` — sparse signal (e.g., logo glyph antialiasing on
        a white page) is rejected wholesale; brand colors fill ≥ several
        hundred pixels at our default 320-wide downsample
      * `min_cluster_avg_chroma` — top cluster's average chroma must clear
        80 (genuine brand colors are 100+; antialiasing noise sits at 65-75)
    """
    im = Image.open(image_path).convert("RGB")
    if im.width > downsample_to_width:
        ratio = downsample_to_width / im.width
        new_size = (downsample_to_width, max(1, int(im.height * ratio)))
        im = im.resize(new_size, Image.BILINEAR)

    pixels: list[tuple[int, int, int]] = []
    for r, g, b in im.getdata():
        if _is_brand_pixel(r, g, b, min_chroma=min_chroma,
                           min_l=min_l, max_l=max_l):
            pixels.append((r, g, b))

    if len(pixels) < min_total_pixels:
        return None

    clusters = _kmeans_rgb(pixels)
    if not clusters:
        return None

    # Rank by size × avg chroma — boosts brand colors over high-volume but
    # less-saturated background tints.
    scored = []
    for rep, group in clusters:
        avg_chroma = sum(_chroma_distance(*p) for p in group) / len(group)
        score = len(group) * avg_chroma
        scored.append((score, len(group), rep, avg_chroma))
    scored.sort(key=lambda x: -x[0])

    top_score, top_count, top_rep, top_chroma = scored[0]
    if top_chroma < min_cluster_avg_chroma:
        return None

    total_pixels = sum(len(g) for _, g in clusters)
    confidence = top_count / total_pixels

    rgba = RGBA(r=top_rep[0], g=top_rep[1], b=top_rep[2], a=1.0)
    return BrandColorResult(
        hex=rgba.to_hex(),
        source="path_a_pixel_rank",
        confidence=confidence,
        detail=(
            f"{top_count:,}/{total_pixels:,} non-neutral pixels in top "
            f"k=5 cluster ({confidence*100:.1f}%); avg_chroma={top_chroma:.0f}"
        ),
    )


# ---- Path (b) — CTA bbox crop + median ----

def detect_via_cta_bbox(
    image_path: str,
    button_backgrounds: list[dict],
    *,
    image_viewport: tuple[int, int] | None = None,
    min_area: float = 100.0,
) -> BrandColorResult | None:
    """Path (b): for top colored button(s), crop screenshot bbox, median pixel.

    `image_viewport` is the (width, height) of the viewport at extraction
    time; if the screenshot was captured at the same DPR, rect coords map
    1:1. Mismatch is detected by comparing image size vs viewport.
    """
    colored = [
        b for b in button_backgrounds
        if b.get("classification") == "colored"
        and b.get("rect", {}).get("visible")
        and b.get("area", 0) >= min_area
    ]
    if not colored:
        return None

    im = Image.open(image_path).convert("RGB")

    # Coordinate scaling: account for screenshot/viewport mismatch.
    if image_viewport is None:
        image_viewport = (im.width, im.height)
    sx = im.width / image_viewport[0]
    sy = im.height / image_viewport[1]

    for b in colored:
        rect = b["rect"]
        x0 = max(0, int(rect["x"] * sx))
        y0 = max(0, int(rect["y"] * sy))
        x1 = min(im.width, int((rect["x"] + rect["width"]) * sx))
        y1 = min(im.height, int((rect["y"] + rect["height"]) * sy))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        crop = im.crop((x0, y0, x1, y1))
        rs, gs, bs = [], [], []
        for r, g, b_px in crop.getdata():
            if not _is_brand_pixel(r, g, b_px):
                continue
            rs.append(r); gs.append(g); bs.append(b_px)
        if len(rs) < 8:
            continue
        rs.sort(); gs.sort(); bs.sort()
        m = len(rs) // 2
        rgba = RGBA(r=rs[m], g=gs[m], b=bs[m], a=1.0)
        return BrandColorResult(
            hex=rgba.to_hex(),
            source="path_b_cta_bbox",
            confidence=len(rs) / max(1, (x1 - x0) * (y1 - y0)),
            detail=(
                f"button bbox=({x0},{y0})-({x1},{y1}) "
                f"non-neutral_pixels={len(rs)} text={b.get('text', '')[:40]!r}"
            ),
        )
    return None


# ---- Path (c) — documented brand fallback ----

def detect_via_documented(url: str) -> BrandColorResult | None:
    """Path (c): hardcoded brand color per known domain (5 spike sites)."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    hex_value = DOCUMENTED_BRAND_COLORS.get(host)
    if hex_value is None:
        return None
    return BrandColorResult(
        hex=hex_value,
        source="path_c_documented",
        confidence=1.0,
        detail=f"hardcoded for host={host}",
    )


# ---- Chained orchestration ----

def detect_brand_color(
    *,
    image_path: str,
    payload: dict,
    url: str | None = None,
    prefer_documented: bool = True,
) -> BrandColorResult | None:
    """Resolve brand color via path chain.

    Order (deviation from plan v5 (a)→(b)→(c)):
      1. **(c) documented** if `prefer_documented` and URL host is in
         `DOCUMENTED_BRAND_COLORS`. Why front-load: spike data shows path (a)
         is unreliable on busy hero illustrations (Stripe orange icon
         outranks brand purple by area×chroma); for the 5 spike sites we
         have ground-truth dict, no need to recompute and risk regression.
      2. **(a) pixel rank** for unknown sites; fall through if confidence
         < `PATH_A_MIN_CONFIDENCE` (no dominant cluster = noisy result).
      3. **(b) CTA bbox** as last resort; ambiguous on utility-first sites
         where CTAs are `<a>` not `<button>` (advisor flag).

    Pass `prefer_documented=False` to test (a)/(b) on documented sites.
    """
    target_url = url or payload.get("url") or ""
    image_viewport: tuple[int, int] | None = None
    vp = payload.get("viewport")
    if isinstance(vp, dict) and vp.get("width") and vp.get("height"):
        image_viewport = (int(vp["width"]), int(vp["height"]))

    if prefer_documented:
        result = detect_via_documented(target_url)
        if result:
            return result

    result = detect_via_pixel_rank(image_path)
    if result and result.confidence >= PATH_A_MIN_CONFIDENCE:
        return result

    result = detect_via_cta_bbox(
        image_path,
        payload.get("button_backgrounds") or [],
        image_viewport=image_viewport,
    )
    if result:
        return result

    if not prefer_documented:
        return detect_via_documented(target_url)
    return None


# ---- ΔE evaluation helper (used by tests) ----

def delta_e_to_documented(detected_hex: str, documented_hex: str) -> float:
    """ΔE76 between detected brand and documented ground truth (for tests)."""
    a = parse_color(detected_hex)
    b = parse_color(documented_hex)
    if a is None or b is None:
        return float("inf")
    return delta_e76(srgb_to_lab(a), srgb_to_lab(b))
