"""Color parsing, sRGB↔Lab conversion, and ΔE76-based dedupe.

Per design.md D1, ΔE < 6 is the perceptual-equivalence threshold. We use the
classical ΔE76 (Euclidean in CIELab) which is fast and accurate enough for
dedupe — ΔE2000 would be more perceptually uniform but adds 50 lines for
~negligible quality gain at this threshold.

v1 scope: native dedupe handles `#hex` (3/6/8 digits) and `rgb()/rgba()`.
Other spaces (oklab/oklch/lab/color()) are passed through untouched —
extractor labels them with `space=...` so Token Registry Resolver (1.7) can
decide whether to evaluate them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

# Color literal regexes
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(
    r"^rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+))?\s*\)$"
)
_LAB_RE = re.compile(
    r"^lab\(\s*([\d.+-]+)(%?)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*/\s*([\d.+-]+))?\s*\)$"
)
_OKLAB_RE = re.compile(
    r"^oklab\(\s*([\d.+-]+)(%?)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*/\s*([\d.+-]+))?\s*\)$"
)
_OKLCH_RE = re.compile(
    r"^oklch\(\s*([\d.+-]+)(%?)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*/\s*([\d.+-]+))?\s*\)$"
)

# Colors that are useless as design tokens. We drop them entirely from dedupe.
_NEUTRAL_DROP_VALUES = frozenset({"transparent", "currentcolor", "inherit", "initial"})

# Subset of CSS named colors that frequently appear in real sites' :root vars.
_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "maroon": (128, 0, 0),
    "olive": (128, 128, 0),
    "purple": (128, 0, 128),
    "teal": (0, 128, 128),
    "navy": (0, 0, 128),
}


@dataclass(frozen=True)
class RGBA:
    r: int  # 0..255
    g: int
    b: int
    a: float  # 0..1

    def to_hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"


@dataclass(frozen=True)
class ColorCluster:
    representative: str       # hex of representative (highest-frequency member)
    frequency: int
    members: tuple[str, ...] = field(default_factory=tuple)


def parse_color(raw: str) -> RGBA | None:
    """Parse a CSS color literal into RGBA. Returns None when unparseable.

    Supported formats:
      - `#hex` (3 / 6 / 8 digits)
      - `rgb()` / `rgba()`
      - `lab()` (L percent or 0..100, a/b ±125)
      - `oklab()` (L 0..1 or %, a/b ±0.5)
      - `oklch()` (L 0..1 or %, C 0..0.4, H degrees)
      - 16 basic CSS named colors (`black`, `white`, ...)
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if s in _NEUTRAL_DROP_VALUES:
        return None

    if s in _NAMED_COLORS:
        r, g, b = _NAMED_COLORS[s]
        return RGBA(r=r, g=g, b=b, a=1.0)

    m = _HEX_RE.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            r, g, b, a = int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 1.0
        elif len(h) == 6:
            r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
        else:
            r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
            a = int(h[6:8], 16) / 255
        return RGBA(r=r, g=g, b=b, a=a)

    m = _RGB_RE.match(s)
    if m:
        r = int(round(float(m.group(1))))
        g = int(round(float(m.group(2))))
        b = int(round(float(m.group(3))))
        a = 1.0 if m.group(4) is None else float(m.group(4))
        return RGBA(r=r, g=g, b=b, a=a)

    m = _LAB_RE.match(s)
    if m:
        L = float(m.group(1))
        if m.group(2) == "%":  # CSS lab() L is 0..100, but % means same scale
            pass
        a = float(m.group(3))
        bb = float(m.group(4))
        alpha = 1.0 if m.group(5) is None else float(m.group(5))
        return _lab_to_rgba(L, a, bb, alpha)

    m = _OKLAB_RE.match(s)
    if m:
        L_raw = float(m.group(1))
        L = L_raw / 100 if m.group(2) == "%" else L_raw
        a = float(m.group(3))
        bb = float(m.group(4))
        alpha = 1.0 if m.group(5) is None else float(m.group(5))
        return _oklab_to_rgba(L, a, bb, alpha)

    m = _OKLCH_RE.match(s)
    if m:
        L_raw = float(m.group(1))
        L = L_raw / 100 if m.group(2) == "%" else L_raw
        c = float(m.group(3))
        h = float(m.group(4))
        alpha = 1.0 if m.group(5) is None else float(m.group(5))
        # oklch -> oklab via polar->rect.
        import math
        a = c * math.cos(math.radians(h))
        bb = c * math.sin(math.radians(h))
        return _oklab_to_rgba(L, a, bb, alpha)

    return None


# ---- Wide-gamut → sRGB conversion helpers ----

def _clip_byte(x: float) -> int:
    return max(0, min(255, int(round(x))))


def _srgb_encode_channel(linear: float) -> float:
    if linear <= 0.0031308:
        return 12.92 * linear * 255
    return (1.055 * (linear ** (1 / 2.4)) - 0.055) * 255


def _lab_to_rgba(L: float, a: float, b: float, alpha: float) -> RGBA:
    # Lab -> XYZ -> linear sRGB -> sRGB (gamma-encoded 0..255).
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    def f_inv(t: float) -> float:
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787
    X = _D65[0] * f_inv(fx)
    Y = _D65[1] * f_inv(fy)
    Z = _D65[2] * f_inv(fz)
    # XYZ (D65) -> linear sRGB
    rl =  3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z
    gl = -0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z
    bl =  0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z
    r = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, rl))))
    g = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, gl))))
    bb = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, bl))))
    return RGBA(r=r, g=g, b=bb, a=alpha)


def _oklab_to_rgba(L: float, a: float, b: float, alpha: float) -> RGBA:
    # Björn Ottosson's OKLab → linear sRGB (D65) reference matrices.
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ ** 3
    m = m_ ** 3
    sx = s_ ** 3
    rl =  4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * sx
    gl = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * sx
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * sx
    r = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, rl))))
    g = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, gl))))
    bb = _clip_byte(_srgb_encode_channel(max(0.0, min(1.0, bl))))
    return RGBA(r=r, g=g, b=bb, a=alpha)


# ---- sRGB → CIELab via XYZ (D65 reference white) ----

# D65 reference white tristimulus values (Y normalized to 1.0, common CIE Lab convention).
_D65 = (0.95047, 1.00000, 1.08883)
# sRGB → XYZ (D65) matrix (linear sRGB inputs).
_M_RGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)


def _srgb_decode_channel(c: int) -> float:
    """Gamma-decode an 8-bit sRGB channel to linear [0, 1]."""
    n = c / 255
    return n / 12.92 if n <= 0.04045 else ((n + 0.055) / 1.055) ** 2.4


def srgb_to_lab(rgba: RGBA) -> tuple[float, float, float]:
    """Convert RGBA (alpha ignored) to CIELab L*a*b*."""
    r = _srgb_decode_channel(rgba.r)
    g = _srgb_decode_channel(rgba.g)
    b = _srgb_decode_channel(rgba.b)
    x = _M_RGB_TO_XYZ[0][0] * r + _M_RGB_TO_XYZ[0][1] * g + _M_RGB_TO_XYZ[0][2] * b
    y = _M_RGB_TO_XYZ[1][0] * r + _M_RGB_TO_XYZ[1][1] * g + _M_RGB_TO_XYZ[1][2] * b
    z = _M_RGB_TO_XYZ[2][0] * r + _M_RGB_TO_XYZ[2][1] * g + _M_RGB_TO_XYZ[2][2] * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx = f(x / _D65[0])
    fy = f(y / _D65[1])
    fz = f(z / _D65[2])
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    bb = 200 * (fy - fz)
    return (L, a, bb)


def delta_e76(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """Euclidean ΔE in CIELab space."""
    return ((lab1[0] - lab2[0]) ** 2 + (lab1[1] - lab2[1]) ** 2 + (lab1[2] - lab2[2]) ** 2) ** 0.5


# ---- Dedupe pipeline ----

def dedupe_colors(
    raw_colors: Iterable[str],
    *,
    delta_e_threshold: float = 6.0,
    drop_alpha_below: float = 0.5,
) -> list[ColorCluster]:
    """Greedy single-link dedupe on a bag of color strings.

    Process:
    1. Parse + filter (drop unparseable, drop alpha < threshold).
    2. Count frequencies of each parsed RGB triplet.
    3. Sort by frequency desc; greedily attach each to the nearest existing
       cluster within ΔE < threshold (else seed a new cluster).
    4. Cluster representative = highest-frequency member's hex.

    The greedy single-link approach is intentional — small ΔE threshold means
    clusters stay perceptually tight, and ranking by frequency means the
    "most-used" hex naturally becomes the canonical name.
    """
    parsed: list[RGBA] = []
    for c in raw_colors:
        rgba = parse_color(c)
        if rgba is None:
            continue
        if rgba.a < drop_alpha_below:
            continue
        parsed.append(rgba)

    counts = Counter((p.r, p.g, p.b) for p in parsed)
    if not counts:
        return []

    ordered = counts.most_common()
    cluster_labs: list[tuple[float, float, float]] = []
    cluster_reps: list[tuple[int, int, int]] = []
    cluster_freqs: list[int] = []
    cluster_members: list[list[str]] = []

    for (r, g, b), freq in ordered:
        rgba = RGBA(r=r, g=g, b=b, a=1.0)
        lab = srgb_to_lab(rgba)
        # Find nearest existing cluster.
        best_i, best_d = -1, float("inf")
        for i, ref_lab in enumerate(cluster_labs):
            d = delta_e76(lab, ref_lab)
            if d < best_d:
                best_i, best_d = i, d
        if best_i >= 0 and best_d < delta_e_threshold:
            cluster_freqs[best_i] += freq
            cluster_members[best_i].append(rgba.to_hex())
        else:
            cluster_labs.append(lab)
            cluster_reps.append((r, g, b))
            cluster_freqs.append(freq)
            cluster_members.append([rgba.to_hex()])

    out: list[ColorCluster] = []
    for rep, freq, members in zip(cluster_reps, cluster_freqs, cluster_members):
        out.append(ColorCluster(
            representative=RGBA(r=rep[0], g=rep[1], b=rep[2], a=1.0).to_hex(),
            frequency=freq,
            members=tuple(members),
        ))
    out.sort(key=lambda c: (-c.frequency, c.representative))
    return out


def collect_color_strings(payload: dict) -> list[str]:
    """Pull every color-shaped string out of an extractor payload.

    Sources, in order: root_vars values, computed_styles color/background-color,
    button_backgrounds background_color. Multiset preserved (frequency matters).
    """
    out: list[str] = []
    for v in (payload.get("root_vars") or {}).values():
        if isinstance(v, str):
            out.append(v.strip())
    for s in payload.get("computed_styles") or []:
        for f in ("color", "background-color"):
            v = s.get(f)
            if isinstance(v, str) and v:
                out.append(v.strip())
    for b in payload.get("button_backgrounds") or []:
        v = b.get("background_color")
        if isinstance(v, str) and v:
            out.append(v.strip())
    return out
