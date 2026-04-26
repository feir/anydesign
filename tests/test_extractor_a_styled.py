"""Tests for D1 AC #4 closeout — <a> button-styled traversal in extractor JS.

Two test layers:

1. **JS string contract** — verifies key heuristic tokens are present in the
   compiled JS. Cheap, no browser dependency. Catches regression of the
   filter clauses (NAV exclusion, area / padding thresholds, role+chroma OR).

2. **Real-browser integration** — composite synthetic data: URL exercises
   filter logic against actual layout. Marked `@pytest.mark.skipif`,
   skipped when agent-browser is not on PATH.
"""

from __future__ import annotations

import shutil
from urllib.parse import quote

import pytest

from design_from_url.extractor import (
    DEFAULT_COMPUTED_SELECTORS,
    _SAMPLES_PER_SELECTOR,
    _build_extraction_js,
    extract_from_url,
)


_AB_AVAILABLE = shutil.which("agent-browser") is not None


# ----------------------------------------------------------------------
# 1. JS string contract — fast, no browser
# ----------------------------------------------------------------------


@pytest.fixture
def extraction_js() -> str:
    return _build_extraction_js(DEFAULT_COMPUTED_SELECTORS, _SAMPLES_PER_SELECTOR)


def test_js_includes_a_traversal_block(extraction_js: str):
    assert "document.querySelectorAll('a')" in extraction_js


def test_js_a_traversal_wrapped_in_try_catch(extraction_js: str):
    """`<a>` traversal must be try/catch-wrapped — heuristic failure here
    cannot kill the primary `<button>` path."""
    a_idx = extraction_js.index("document.querySelectorAll('a')")
    pre_500 = extraction_js[max(0, a_idx - 500): a_idx]
    assert "try {" in pre_500


def test_js_includes_in_nav_helper(extraction_js: str):
    """In-nav exclusion is required by D1 4-clause filter clause (2)."""
    assert "tagName === 'NAV'" in extraction_js


def test_js_does_not_exclude_header(extraction_js: str):
    """Stripe / Linear hero CTAs live in `<header>`; must NOT be excluded.
    Only `<nav>` ancestor should bar inclusion."""
    assert "tagName === 'HEADER'" not in extraction_js


def test_js_min_area_threshold(extraction_js: str):
    """Filter clause (1): area >= 3000."""
    assert "area < 3000" in extraction_js


def test_js_min_padding_threshold(extraction_js: str):
    """Filter clause (3): padMax >= 8."""
    assert "padMax < 8" in extraction_js


def test_js_role_button_or_chroma_clause(extraction_js: str):
    """Filter clause (4): role=button OR chroma >= threshold."""
    assert "isRoleButton" in extraction_js
    assert "chromaOK" in extraction_js
    assert "!isRoleButton && !chromaOK" in extraction_js


def test_js_button_entries_carry_source_field(extraction_js: str):
    """Both `<button>` and `<a-styled>` entries tagged with source."""
    assert extraction_js.count("source: 'button'") == 1
    assert extraction_js.count("source: 'a-styled'") == 1


def test_js_uses_longhand_padding_properties(extraction_js: str):
    """CSSOM spec: shorthand `padding` returns empty when set via longhand.
    Must read individual sides for cross-browser correctness."""
    assert "'padding-top'" in extraction_js
    assert "'padding-right'" in extraction_js
    assert "'padding-bottom'" in extraction_js
    assert "'padding-left'" in extraction_js


# ----------------------------------------------------------------------
# 2. Real-browser integration — composite fixture
# ----------------------------------------------------------------------


_COMPOSITE_HTML = """\
<!doctype html><html><head><style>
  body { margin: 0; font-family: sans-serif; padding: 20px; }
  .cta-hero { display: inline-block; background: #635bff; color: white;
              padding: 14px 28px; text-decoration: none;
              border-radius: 4px; }
  .cta-role { display: inline-block; background: #f0f0f0; color: black;
              padding: 14px 28px; text-decoration: none; }
  nav a { padding: 4px 8px; }
  .real-btn { background: #0066ff; color: white; padding: 10px 20px;
              border: 0; font-size: 14px; }
  .text-link { padding: 0; }
</style></head><body>
  <header>
    <a class="cta-hero" href="#hero">Hero CTA in header</a>
  </header>
  <main>
    <button class="real-btn">Real Button</button>
    <a class="cta-role" role="button" href="#role">Role Button Anchor</a>
    <a class="text-link" href="#plain">Plain Text Link</a>
  </main>
  <nav>
    <a class="cta-hero" href="#nav">Nav Link Should Skip</a>
  </nav>
</body></html>
"""


@pytest.mark.skipif(
    not _AB_AVAILABLE,
    reason="agent-browser not on PATH — composite real-browser test skipped",
)
def test_a_traversal_filter_against_real_browser():
    """Composite data: URL exercises all 4 filter clauses in one run.

    Expected entries in `button_backgrounds`:
      - 1 source='button'   → Real Button (existing path)
      - 1 source='a-styled' → Hero CTA in header (clause 2: <header> allowed)
      - 1 source='a-styled' → Role Button Anchor (clause 4: role=button OR)
    Excluded:
      - Nav Link Should Skip (clause 2: under <nav>)
      - Plain Text Link (clause 3: padding 0)
    """
    url = "data:text/html;charset=utf-8," + quote(_COMPOSITE_HTML)
    payload = extract_from_url(url, timeout_s=20, dismiss_consent=False)
    btns = payload["button_backgrounds"]
    by_text = {b["text"]: b for b in btns}

    # Inclusions
    assert "Real Button" in by_text, f"missing Real Button; got {list(by_text)}"
    assert by_text["Real Button"]["source"] == "button"

    assert "Hero CTA in header" in by_text, (
        f"<header> hero CTA wrongly excluded; got {list(by_text)}"
    )
    assert by_text["Hero CTA in header"]["source"] == "a-styled"

    assert "Role Button Anchor" in by_text, (
        f"role=button anchor wrongly excluded; got {list(by_text)}"
    )
    assert by_text["Role Button Anchor"]["source"] == "a-styled"

    # Exclusions
    assert "Nav Link Should Skip" not in by_text, (
        "nav link wrongly included — inNav check failed"
    )
    assert "Plain Text Link" not in by_text, (
        "padding-0 anchor wrongly included — padMax<8 check failed"
    )

    # Total count is exactly 3 (no other anchors/buttons match the filter)
    assert len(btns) == 3, f"expected 3 entries, got {len(btns)}: {btns}"
