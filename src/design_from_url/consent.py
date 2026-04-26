"""Consent / cookie banner dismissal via agent-browser.

Strategy: try a small ordered list of well-known accept-button selectors. After
clicking the first one that exists *and* is visible, run a short JS Promise
that waits for body height to stay stable (proxy for "banner removed and
layout settled") before returning. Best-effort — never raises on failure
since most pages have no banner at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from design_from_url.renderer import BrowserSession


# Ordered by specificity (id-based first) then heuristic.
CONSENT_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    "[id*='cookie-accept']",
    "[aria-label*='Accept']",
    "button:has-text('Accept all')",
    "button:has-text('接受全部')",
)

# JS that finds the first matching consent button and clicks it. Returns the
# selector that fired, or null if none matched. Uses :has-text emulation
# because plain DOM doesn't support it — we look at button textContent.
_PROBE_AND_CLICK_JS = r"""
(() => {
    const cssSelectors = [
        '#onetrust-accept-btn-handler',
        "[id*='cookie-accept']",
        "[aria-label*='Accept']",
    ];
    const textSelectors = [
        { tag: 'button', text: 'Accept all' },
        { tag: 'button', text: '接受全部' },
    ];
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none') return false;
        return true;
    };
    for (const sel of cssSelectors) {
        let el;
        try { el = document.querySelector(sel); } catch (_) { continue; }
        if (visible(el)) { el.click(); return { matched: sel }; }
    }
    for (const { tag, text } of textSelectors) {
        const candidates = Array.from(document.querySelectorAll(tag));
        const hit = candidates.find(
            (el) => visible(el) && (el.textContent || '').trim().includes(text)
        );
        if (hit) { hit.click(); return { matched: `${tag}:has-text("${text}")` }; }
    }
    return { matched: null };
})()
"""

# Promise-returning JS: resolves once body.scrollHeight stays within
# `tolerancePx` for `windowMs`, or after `timeoutMs` regardless.
_HEIGHT_STABLE_JS = r"""
(async () => {
    const windowMs = 500;
    const tolerancePx = 5;
    const timeoutMs = 3000;
    const samples = [];
    const start = Date.now();
    return await new Promise((resolve) => {
        const tick = () => {
            const now = Date.now();
            const h = document.body ? document.body.scrollHeight : 0;
            samples.push({ t: now, h });
            while (samples.length > 1 && now - samples[0].t > windowMs) {
                samples.shift();
            }
            if (samples.length >= 2 && now - samples[0].t >= windowMs) {
                const min = Math.min(...samples.map(s => s.h));
                const max = Math.max(...samples.map(s => s.h));
                if (max - min <= tolerancePx) {
                    resolve({ stable: true, height: h });
                    return;
                }
            }
            if (now - start > timeoutMs) {
                resolve({ stable: false, height: h });
                return;
            }
            setTimeout(tick, 50);
        };
        tick();
    });
})()
"""


@dataclass(frozen=True)
class ConsentResult:
    dismissed: bool
    selector: str | None
    stabilized: bool


def dismiss_consent(session: "BrowserSession") -> ConsentResult:
    """Run probe-and-click in-page, then wait for layout stability."""
    probe = session.eval_js(_PROBE_AND_CLICK_JS) or {}
    matched = probe.get("matched")
    if not matched:
        return ConsentResult(dismissed=False, selector=None, stabilized=False)

    stability = session.eval_js(_HEIGHT_STABLE_JS) or {}
    return ConsentResult(
        dismissed=True,
        selector=matched,
        stabilized=bool(stability.get("stable", False)),
    )
