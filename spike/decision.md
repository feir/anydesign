# Phase 3a Spike — Decisions Log

## 3a.1 Dark Mode Capability Probe (2026-04-26)

**Result: PASS** — agent-browser supports stateful `prefers-color-scheme` emulation.

**Environment**: agent-browser 0.26.0 (macOS Darwin 25.3.0)

**Method**: Synthetic HTML with CSS custom property `--c` set via `:root` and overridden in `@media (prefers-color-scheme: dark)`. Single `<div>` with inline `style="color: var(--c)"`. Probe sequence:

1. Open `file:///tmp/dfu-probe.html` (red default, blue dark)
2. eval `getComputedStyle(...).color` → `"rgb(255, 0, 0)"` ✓
3. `set media dark`
4. eval → `"rgb(0, 0, 255)"` ✓
5. eval again (persistence) → `"rgb(0, 0, 255)"` ✓
6. `set media light` → eval → `"rgb(255, 0, 0)"` ✓ (reversible)

**Implications for Phase 3a**:
- `BrowserSession.set_color_scheme("dark")` can use `agent-browser set media <scheme>` directly.
- Emulation is stateful across multiple `eval_js` calls in the same session — no per-call re-flip needed.
- `extract_dual_mode` design holds: open page once, set media light, extract, set media dark, extract, close. Same page state across both extractions.
- `MIN_AGENT_BROWSER_VERSION = "0.26.0"` pinned in constants.py (the verified-working version).

**No deferral needed** — Phase 3a ships dark mode unconditionally per AC #2.

---

## Phase 1+2 Architectural Decision Recap

(For Phase 3b 10-site sweep context)

### Renderer pivot (Phase 0 spike)
- Tested: Playwright vs agent-browser
- Chosen: agent-browser (real Chrome, anti-bot resistance, daemon session model)

### Schema fixer 4-cell decision table (Phase 2)
- (has_nearest_ΔE<10 × has_default) → 4 actions: nearest / default / drop / raise
- Required broken-ref fields: never drop, raise if no nearest+default
- Optional: drop ok

### Monochrome fallback (Phase 2 plan-review M5)
- 0 colored buttons → fall back to top-3 by area regardless of chroma
- Handles Vercel #000 CTA case

### button-primary y-cutoff relaxation (Phase 2)
- Original: y < viewport_height × 0.25
- Final: y < viewport_height × 1.0
- Reason: real-site CTAs cluster well below top quarter in document coordinates
