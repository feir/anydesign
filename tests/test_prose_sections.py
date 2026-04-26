"""Unit tests for D4 per-section prose generation + fallback counter.

Coverage:
- `_is_valid` predicate edge cases
- `_build_fallback` outputs MUST NOT contain "deferred"
- `generate_prose_section` retries once on failure
- `generate_all_prose_sections` aggregates fallback_count correctly
"""

from __future__ import annotations

import pytest

from design_from_url import llm as _llm
from design_from_url.prose_sections import (
    PROSE_SECTION_KEYS,
    _build_fallback,
    _is_valid,
    generate_all_prose_sections,
    generate_prose_section,
)


_REGISTRY_YAML = "colors:\n  primary: \"#635bff\"\n"


# ----------------------------------------------------------------------
# _is_valid predicate
# ----------------------------------------------------------------------


def test_is_valid_rejects_none():
    assert _is_valid(None) is False


def test_is_valid_rejects_empty():
    assert _is_valid("") is False


def test_is_valid_rejects_too_short():
    assert _is_valid("short") is False


def test_is_valid_rejects_deferred_echo():
    """LLM-output that echoes the legacy Phase 2 placeholder text must
    be treated as invalid — otherwise self-lint would re-flag it."""
    assert _is_valid("This prose generation deferred to Phase 2.x") is False
    assert _is_valid("PROSE GENERATION DEFERRED TO PHASE 2.X" * 2) is False


def test_is_valid_accepts_substantive_paragraph():
    text = "The brand uses a confident purple primary across CTAs and headings."
    assert _is_valid(text) is True


# ----------------------------------------------------------------------
# _build_fallback content guarantees
# ----------------------------------------------------------------------


@pytest.mark.parametrize("key", PROSE_SECTION_KEYS)
def test_fallback_no_deferred_word(key: str):
    """Fallback stubs MUST NOT contain 'deferred' — that would echo the
    Phase 2 placeholder and recurse the self-lint loop."""
    out = _build_fallback(key)
    assert "deferred" not in out.lower()


@pytest.mark.parametrize("key", PROSE_SECTION_KEYS)
def test_fallback_substantive(key: str):
    """Fallback must satisfy the same _is_valid check it bypasses."""
    out = _build_fallback(key)
    assert _is_valid(out) is True


def test_fallback_unknown_key_returns_default():
    out = _build_fallback("nonexistent_section")
    assert "nonexistent_section" in out
    assert "deferred" not in out.lower()


# ----------------------------------------------------------------------
# generate_prose_section — single-section retry contract
# ----------------------------------------------------------------------


def _gen_prose(key: str, llm_generate):
    return generate_prose_section(
        key,
        registry_yaml=_REGISTRY_YAML,
        screenshot_path=None,
        model="local/test",
        llm_generate=llm_generate,
    )


def test_first_attempt_valid_no_fallback():
    """Valid first-attempt output → no retry, no fallback."""
    calls = []
    def fake(prompt, **kw):
        calls.append(prompt)
        return "A purple-led palette with neutral surfaces and accents for emphasis."
    text, fell_back = _gen_prose("colors_prose", fake)
    assert fell_back is False
    assert "purple" in text
    assert len(calls) == 1


def test_first_attempt_invalid_then_retry_succeeds():
    """First attempt invalid (too short) → retries; second valid → no fallback."""
    state = {"n": 0}
    def fake(prompt, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return "x"  # too short, invalid
        return "Brand uses a primary purple across CTAs and headings."
    text, fell_back = _gen_prose("colors_prose", fake)
    assert fell_back is False
    assert state["n"] == 2
    assert "Brand uses" in text


def test_both_attempts_invalid_uses_fallback():
    """Both attempts invalid → fallback used, fall_back=True."""
    def fake(prompt, **kw):
        return "x"  # always too short
    text, fell_back = _gen_prose("typography_prose", fake)
    assert fell_back is True
    assert "deferred" not in text.lower()
    assert _is_valid(text) is True


def test_llm_unavailable_both_attempts_uses_fallback():
    """LLMUnavailable raised twice → fallback (NOT propagated)."""
    def fake(prompt, **kw):
        raise _llm.LLMUnavailable("cloud has no image")
    text, fell_back = _gen_prose("layout_prose", fake)
    assert fell_back is True
    assert _is_valid(text)


def test_other_exception_treated_as_failure():
    """Any non-LLMUnavailable exception is also treated as attempt failure."""
    def fake(prompt, **kw):
        raise RuntimeError("model glitch")
    text, fell_back = _gen_prose("components_prose", fake)
    assert fell_back is True


def test_first_attempt_exception_then_retry_succeeds():
    """LLMUnavailable on first attempt; retry returns valid → no fallback."""
    state = {"n": 0}
    def fake(prompt, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise _llm.LLMUnavailable("transient")
        return "Layout uses generous spacing with rounded surfaces and clear rhythm."
    text, fell_back = _gen_prose("layout_prose", fake)
    assert fell_back is False
    assert state["n"] == 2


def test_deferred_echo_treated_as_invalid_falls_back():
    """LLM that echoes 'deferred' is treated as invalid → fallback used."""
    def fake(prompt, **kw):
        return "Prose generation deferred to Phase 2.x — see registry."
    text, fell_back = _gen_prose("colors_prose", fake)
    assert fell_back is True
    assert "deferred" not in text.lower()


# ----------------------------------------------------------------------
# generate_all_prose_sections — fallback aggregation
# ----------------------------------------------------------------------


def test_all_4_valid_returns_count_0():
    def fake(prompt, **kw):
        return "Substantive prose paragraph that is long enough to pass validation."
    texts, count = generate_all_prose_sections(
        registry_yaml=_REGISTRY_YAML,
        screenshot_path=None,
        model="local/test",
        llm_generate=fake,
    )
    assert count == 0
    assert set(texts) == set(PROSE_SECTION_KEYS)
    for v in texts.values():
        assert _is_valid(v)


def test_one_section_fails_returns_count_1():
    """1 section fails both attempts → count=1, others valid."""
    state = {"n": 0}
    def fake(prompt, **kw):
        state["n"] += 1
        # First section: 2 attempts → invalid both
        # Subsequent: valid
        if state["n"] <= 2:
            return "x"
        return "Substantive prose paragraph that is long enough to pass validation."
    texts, count = generate_all_prose_sections(
        registry_yaml=_REGISTRY_YAML,
        screenshot_path=None,
        model="local/test",
        llm_generate=fake,
    )
    assert count == 1


def test_two_sections_fail_returns_count_2():
    """2 sections fail → count=2 → triggers prose_partial in caller."""
    state = {"n": 0}
    def fake(prompt, **kw):
        state["n"] += 1
        # Sections 1 and 2: 2 attempts each (calls 1-4) → invalid
        # Sections 3-4: valid
        if state["n"] <= 4:
            return ""
        return "Substantive prose paragraph that is long enough to pass validation."
    texts, count = generate_all_prose_sections(
        registry_yaml=_REGISTRY_YAML,
        screenshot_path=None,
        model="local/test",
        llm_generate=fake,
    )
    assert count == 2
    # All 4 sections still populated (invalid ones used fallback)
    assert len(texts) == 4
    for v in texts.values():
        assert _is_valid(v)


def test_all_4_fail_returns_count_4():
    """LLM totally broken → all 4 sections fall back, count=4."""
    def fake(prompt, **kw):
        raise _llm.LLMUnavailable("down")
    texts, count = generate_all_prose_sections(
        registry_yaml=_REGISTRY_YAML,
        screenshot_path=None,
        model="local/test",
        llm_generate=fake,
    )
    assert count == 4
    assert len(texts) == 4
    for v in texts.values():
        assert _is_valid(v)
        assert "deferred" not in v.lower()
