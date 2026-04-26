"""Extract raw design signals from a rendered page.

Three extraction passes (all run as one big JS payload via agent-browser
`eval --stdin --json`):

1. `:root` CSS custom properties (`--*` values) — the highest-signal source
   when a site exposes a design-token API.
2. Computed styles for representative typographic / interactive elements —
   covers utility-CSS sites that don't publish `--*` vars.
3. Dedicated `<button>` traversal — captures CTA backgrounds that get class-
   injected (Tailwind / Notion pattern). Carries bounding rects for Phase
   1.5b CTA-bbox brand-pixel sampling.

Output is a JSON-serializable dict; aggregation (color dedupe, clustering,
registry build) is the next layer.
"""

from __future__ import annotations

import json
from typing import Any


# Selectors targeted by the computed-style sweep. Each yields up to
# `_SAMPLES_PER_SELECTOR` samples (in DOM order) to bound payload size.
DEFAULT_COMPUTED_SELECTORS: tuple[str, ...] = (
    "h1", "h2", "h3", "body", "button", "a", "input",
)
_SAMPLES_PER_SELECTOR = 5

# CSS properties read for each computed-style sample.
_COMPUTED_PROPS: tuple[str, ...] = (
    "color",
    "background-color",
    "font-family",
    "font-size",
    "font-weight",
    "line-height",
    "letter-spacing",
    "border-radius",
    "padding",
)

# Browser-default values to flag (kept in payload, but tagged for downstream
# filtering — the aggregator decides whether to drop).
_DEFAULT_COLOR_VALUES = frozenset({
    "rgb(0, 0, 0)",
    "rgba(0, 0, 0, 0)",
})
_DEFAULT_FONT_FAMILIES_FRAGMENTS = ("times", "-webkit-standard", "monospace")


def _build_extraction_js(
    selectors: tuple[str, ...], samples_per_selector: int
) -> str:
    # Args are inlined as JSON literals because agent-browser eval --stdin
    # accepts only a script body (no companion args mechanism).
    args_json = json.dumps({
        "selectors": list(selectors),
        "samplesPerSelector": samples_per_selector,
        "props": list(_COMPUTED_PROPS),
    })
    return r"""
(({ selectors, samplesPerSelector, props }) => {
    // ---- 1. :root CSS custom properties ----
    const rootStyle = getComputedStyle(document.documentElement);
    const rootVars = {};
    for (let i = 0; i < rootStyle.length; i++) {
        const name = rootStyle[i];
        if (name && name.startsWith('--')) {
            rootVars[name] = rootStyle.getPropertyValue(name).trim();
        }
    }

    // ---- 2. Computed styles for representative elements ----
    const computed = [];
    for (const sel of selectors) {
        let nodes;
        try { nodes = document.querySelectorAll(sel); }
        catch (_) { continue; }
        const limit = Math.min(nodes.length, samplesPerSelector);
        for (let i = 0; i < limit; i++) {
            const node = nodes[i];
            const cs = getComputedStyle(node);
            const sample = { selector: sel, sample_index: i };
            for (const prop of props) {
                sample[prop] = cs.getPropertyValue(prop).trim();
            }
            const r = node.getBoundingClientRect();
            sample.rect = {
                x: r.x, y: r.y, width: r.width, height: r.height,
                visible: r.width > 0 && r.height > 0,
            };
            computed.push(sample);
        }
    }

    // ---- 3. Dedicated <button> traversal ----
    // Color parsing covers the four formats real sites emit in 2026:
    //   rgb()/rgba(), oklab(), oklch(), color(srgb|display-p3 ...).
    // Classification only needs alpha + a chroma proxy + lightness; we do not
    // convert across spaces (avoids ~50 lines of matrix math the aggregator
    // can do later if needed).
    const parseColor = (raw) => {
        if (!raw) return null;
        const s = raw.trim();
        // rgb(a)
        let m = s.match(/^rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+))?\s*\)$/);
        if (m) {
            const r = +m[1], g = +m[2], b = +m[3];
            const a = m[4] === undefined ? 1 : +m[4];
            // Chroma proxy: max-min over 255.
            const ml = Math.max(r, g, b) / 255;
            const mn = Math.min(r, g, b) / 255;
            const lightness = (ml + mn) / 2;
            const chroma = ml - mn;
            return { space: 'rgb', r, g, b, a, lightness, chroma };
        }
        // oklab(L a b [/ alpha])
        m = s.match(/^oklab\(\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*\/\s*([\d.+-]+))?\s*\)$/);
        if (m) {
            const L = +m[1], aa = +m[2], bb = +m[3];
            const a = m[4] === undefined ? 1 : +m[4];
            const chroma = Math.sqrt(aa * aa + bb * bb);
            return { space: 'oklab', L, a_axis: aa, b_axis: bb, a, lightness: L, chroma };
        }
        // lab(L a b [/ alpha]) — CIE Lab, L in [0,100], a/b in [-125, 125]
        m = s.match(/^lab\(\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*\/\s*([\d.+-]+))?\s*\)$/);
        if (m) {
            const L = +m[1], aa = +m[2], bb = +m[3];
            const a = m[4] === undefined ? 1 : +m[4];
            const chroma = Math.sqrt(aa * aa + bb * bb);
            return { space: 'lab', L: L / 100, a_axis: aa, b_axis: bb, a, lightness: L / 100, chroma };
        }
        // oklch(L C H [/ alpha])
        m = s.match(/^oklch\(\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*\/\s*([\d.+-]+))?\s*\)$/);
        if (m) {
            const L = +m[1], C = +m[2], H = +m[3];
            const a = m[4] === undefined ? 1 : +m[4];
            return { space: 'oklch', L, C, H, a, lightness: L, chroma: C };
        }
        // color(<space> r g b [/ alpha]) — sRGB-like spaces only
        m = s.match(/^color\(\s*(srgb|display-p3|srgb-linear)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)(?:\s*\/\s*([\d.+-]+))?\s*\)$/);
        if (m) {
            const r = +m[2], g = +m[3], b = +m[4];
            const a = m[5] === undefined ? 1 : +m[5];
            const ml = Math.max(r, g, b);
            const mn = Math.min(r, g, b);
            const lightness = (ml + mn) / 2;
            const chroma = ml - mn;
            return { space: m[1], r, g, b, a, lightness, chroma };
        }
        return null;
    };
    // Thresholds chosen to match perceptual experience across spaces:
    //   rgb chroma is in [0, 1] (max-min normalized); oklab chroma is in
    //   [0, ~0.4] for sRGB gamut. 0.04 separates "colored CTA" from "gray".
    const CHROMA_THRESHOLDS = {
        rgb: 0.04, oklab: 0.04, oklch: 0.04,
        srgb: 0.04, 'display-p3': 0.04, 'srgb-linear': 0.04,
        // CIE Lab chroma is in [0, ~150]; ~7 separates "subtle gray-ish"
        // from "actually colored".
        lab: 7,
    };
    const buttons = [];
    document.querySelectorAll('button').forEach((btn) => {
        const cs = getComputedStyle(btn);
        const bg = cs.getPropertyValue('background-color').trim();
        const rect = btn.getBoundingClientRect();
        const parsed = parseColor(bg);
        let classification = 'parse-failed';
        if (parsed) {
            // alpha < 0.1 captures hover overlays and ghost states which
            // never represent the real CTA brand color even when their
            // underlying chroma is high.
            const thr = CHROMA_THRESHOLDS[parsed.space] ?? 0.04;
            if (parsed.a < 0.1) classification = 'transparent';
            else if (parsed.chroma < thr) classification = 'neutral';
            else classification = 'colored';
        }
        buttons.push({
            background_color: bg,
            color: parsed,
            classification,
            area: Math.max(0, rect.width * rect.height),
            rect: {
                x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                visible: rect.width > 0 && rect.height > 0,
            },
            text: (btn.textContent || '').trim().slice(0, 80),
        });
    });
    buttons.sort((a, b) => {
        const aw = a.classification === 'colored' ? 1 : 0;
        const bw = b.classification === 'colored' ? 1 : 0;
        if (aw !== bw) return bw - aw;
        return b.area - a.area;
    });

    // ---- 4. Length histogram (all visible elements) ----
    // Drives Phase 1.5a numeric clustering. The 7-selector × 5-sample sweep
    // above is too narrow to surface the full spacing scale (e.g. Tailwind's
    // {4, 12, 20, 24, 48} buckets); a whole-DOM walk gives the aggregator
    // enough data to cluster meaningfully. Hidden / zero-rect elements are
    // skipped so spacing reflects what users actually see.
    const lengthFields = ['padding', 'border-radius', 'gap'];
    const lengthHistogram = {};
    for (const f of lengthFields) lengthHistogram[f] = [];
    const lenRe = /(-?\d+(?:\.\d+)?)px\b/g;
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    let scanned = 0;
    for (const el of document.querySelectorAll('*')) {
        if (!visible(el)) continue;
        scanned++;
        const cs = getComputedStyle(el);
        for (const f of lengthFields) {
            const raw = cs.getPropertyValue(f).trim();
            if (!raw) continue;
            const matches = raw.matchAll(lenRe);
            for (const m of matches) {
                const px = +m[1];
                if (px > 0) lengthHistogram[f].push(px);
            }
        }
    }

    return {
        root_vars: rootVars,
        computed_styles: computed,
        button_backgrounds: buttons,
        length_histogram: lengthHistogram,
        length_histogram_meta: { elements_scanned: scanned, fields: lengthFields },
        viewport: {
            width: window.innerWidth,
            height: window.innerHeight,
            dpr: window.devicePixelRatio || 1,
        },
    };
})(""" + args_json + ")"


def _annotate_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Tag computed-style samples whose color/font fields are browser defaults.

    Aggregator can then filter or down-weight without re-parsing strings.
    """
    samples = payload.get("computed_styles", [])
    distinct_non_default = 0
    for s in samples:
        color = s.get("color", "")
        bg = s.get("background-color", "")
        family = (s.get("font-family", "") or "").lower()
        is_default_color = color in _DEFAULT_COLOR_VALUES
        is_default_bg = bg in _DEFAULT_COLOR_VALUES
        is_default_family = any(f in family for f in _DEFAULT_FONT_FAMILIES_FRAGMENTS)
        s["_is_default_color"] = is_default_color
        s["_is_default_bg"] = is_default_bg
        s["_is_default_family"] = is_default_family
        if not (is_default_color and is_default_bg and is_default_family):
            distinct_non_default += 1
    payload["_meta"] = {
        "computed_distinct_non_default": distinct_non_default,
        "root_vars_count": len(payload.get("root_vars", {})),
        "buttons_total": len(payload.get("button_backgrounds", [])),
        "buttons_colored": sum(
            1 for b in payload.get("button_backgrounds", [])
            if b.get("classification") == "colored"
        ),
    }
    return payload


def extract_from_url(
    url: str,
    *,
    timeout_s: int = 30,
    dismiss_consent: bool = True,
    session_name: str | None = None,
    screenshot_path: str | None = None,
) -> dict[str, Any]:
    """End-to-end: render URL, run JS extraction, return annotated dict.

    If `screenshot_path` is provided, a viewport screenshot is captured into
    that path within the same session (avoids reopening the browser later
    for Phase 1.5b brand-color detection).
    """
    from design_from_url.renderer import open_session, DEFAULT_SESSION

    js = _build_extraction_js(DEFAULT_COMPUTED_SELECTORS, _SAMPLES_PER_SELECTOR)
    with open_session(
        url,
        session_name=session_name or DEFAULT_SESSION,
        timeout_s=timeout_s,
        dismiss_consent_banners=dismiss_consent,
    ) as (session, info):
        raw = session.eval_js(js)
        if screenshot_path:
            session.screenshot(screenshot_path)
    raw["url"] = info.final_url
    raw["page_title"] = info.page_title
    raw["html_size"] = info.html_size
    if screenshot_path:
        raw["screenshot_path"] = screenshot_path
    return _annotate_defaults(raw)
