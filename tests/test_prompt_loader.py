"""Tests for prompt_loader.py — Phase 2.3 prompt artifacts + loader.

CRITICAL regression guards (plan-review C2):
- `<<key>>` substitution must work
- Literal `{colors.X}` references must survive load_prompt unchanged
  (str.format would have crashed/escaped them)
"""

from __future__ import annotations

import pytest

from design_from_url.prompt_loader import (
    PROMPTS_DIR, PromptNotFoundError,
    extract_hex_from_yaml_value, load_prompt,
)


# ---- C2 regression: literal {colors.X} survives loader ----

def test_literal_colors_x_survives_load():
    """3 of 4 prompts (overview/components/dos_donts) use `{colors.X}` literal
    token references in their guidance to the LLM. These must survive the
    loader unchanged — str.format would have crashed/escaped them.

    role_mapping.md is excluded: it asks for bare token NAMES (e.g. `color_1`),
    not curly-brace references, since it's defining the names rather than
    consuming them.
    """
    for name in ("overview", "components", "dos_donts"):
        text = load_prompt(name, registry="dummy registry",
                           role_mapping="dummy roles", candidates="dummy candidates")
        assert "{colors." in text, (
            f"prompt {name!r} should reference literal {{colors.X}} "
            f"for the LLM but the loader appears to have stripped it"
        )


def test_substitution_works_for_known_key():
    """<<registry>> placeholder must be replaced with the kwargs value."""
    text = load_prompt("overview", registry="MY_REGISTRY_DATA")
    assert "MY_REGISTRY_DATA" in text
    # Original placeholder must be gone (not partial replacement)
    assert "<<registry>>" not in text


def test_substitution_with_multiple_keys():
    """dos_donts.md uses both <<registry>> and <<role_mapping>>."""
    text = load_prompt("dos_donts",
                       registry="REG_A",
                       role_mapping="ROLES_B")
    assert "REG_A" in text
    assert "ROLES_B" in text
    assert "<<registry>>" not in text
    assert "<<role_mapping>>" not in text


def test_unknown_kwargs_silently_ignored():
    """Unknown keys (no matching <<unknown>> in template) don't raise."""
    text = load_prompt("overview", registry="x", unused_key="y")
    # The unused kwarg simply has no effect
    assert "y" not in text or "registry" in text  # trivially true; just no crash


def test_unknown_prompt_raises():
    with pytest.raises(PromptNotFoundError, match="not found"):
        load_prompt("does_not_exist", registry="x")


def test_all_4_prompt_files_exist():
    """Phase 2.3 deliverable: 4 prompt artifacts."""
    expected = {"overview.md", "role_mapping.md", "components.md", "dos_donts.md"}
    actual = {p.name for p in PROMPTS_DIR.glob("*.md")}
    missing = expected - actual
    assert not missing, f"missing prompts: {missing}"


def test_quoted_hex_example_present_in_role_mapping():
    """Spec D-finding regression guard: prompt must explicitly instruct
    LLM to quote hex (otherwise YAML treats `#` as comment delimiter)."""
    text = load_prompt("role_mapping", registry="x")
    # Match either the explicit instruction or an example showing quoted form
    has_instruction = "Quote" in text or "quote" in text
    has_example = '"#' in text  # e.g. example shows "#xxxxxx"
    assert has_instruction or has_example, (
        "role_mapping prompt missing quoted-hex guidance"
    )


# ---- HEX_PATTERN: 3 / 6 / 8 digit coverage ----

def test_extract_hex_6_digit():
    assert extract_hex_from_yaml_value("primary: #635bff") == ["#635bff"]


def test_extract_hex_quoted():
    assert extract_hex_from_yaml_value('primary: "#635bff"') == ["#635bff"]


def test_extract_hex_3_digit():
    """3-digit shortcut form (e.g. CSS `#fff`)."""
    assert extract_hex_from_yaml_value("color: #fff") == ["#fff"]


def test_extract_hex_8_digit_with_alpha():
    """8-digit form is hex + alpha channel — common in modern CSS."""
    assert extract_hex_from_yaml_value("overlay: #635bff80") == ["#635bff80"]


def test_extract_hex_multiple_in_line():
    text = "two colors: #ff0000 and #00ff00"
    assert extract_hex_from_yaml_value(text) == ["#ff0000", "#00ff00"]


def test_extract_hex_no_match_returns_empty():
    assert extract_hex_from_yaml_value("primary: red") == []
    assert extract_hex_from_yaml_value("") == []


def test_extract_hex_uppercase_preserved():
    """Pattern doesn't lowercase — caller is responsible for normalization."""
    assert extract_hex_from_yaml_value('primary: "#FF0000"') == ["#FF0000"]


def test_extract_hex_avoids_word_boundary_false_positives():
    """Hex inside a longer word/identifier should NOT match (e.g. `#635bff` in
    `#635bffXX` would be ambiguous; we trust the \\b anchor)."""
    # Trailing alphabetic — should not match "#635bffhello" as 6-digit + extra
    text = "weird: #635bffXY"
    matches = extract_hex_from_yaml_value(text)
    # Either matches the full 8-digit (#635bffXY → invalid because XY not hex)
    # or doesn't match at all. What we MUST NOT see is "#635bff" extracted
    # while "XY" is silently dropped.
    if matches:
        for m in matches:
            assert m != "#635bff", (
                "regex extracted #635bff while ignoring trailing non-hex chars; "
                "this would silently misrepresent the color"
            )
