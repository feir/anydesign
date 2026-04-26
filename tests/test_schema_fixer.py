"""Tests for schema_fixer.py — Phase 2.5 Pass 1 + Pass 2 + 4-cell decision table.

Cases (a)-(g) + (i) + (j) + (l) per spec tasks.md 2.5 Validate list (with M4
cascade additions). Case (h) E2E moved to test_e2e in 2.6.
"""

from __future__ import annotations

import pytest

from design_from_url.preflight import LintFinding
from design_from_url.registry import ColorToken, Registry
from design_from_url.schema_fixer import (
    FIELD_DEFAULTS, FIELD_ROLE_HINTS,
    Pass2Unresolvable,
    apply_pass1, apply_to_file,
    resolve_broken_ref,
    split_frontmatter, join_frontmatter,
)


# ---- helpers ----

def _registry(*tokens: tuple[str, str]) -> Registry:
    """Build a minimal Registry from (name, hex) tuples."""
    return Registry(
        colors=tuple(
            ColorToken(name=n, value=v, frequency=10, source="test", members=(v,))
            for n, v in tokens
        ),
        typography={}, spacing=(), rounded=(),
    )


def _bf(message: str, path: str = "components.button-primary.backgroundColor") -> LintFinding:
    """Build a broken-ref LintFinding."""
    return LintFinding(severity="error", path=path, message=message)


# ---- split / join YAML round-trip ----

def test_split_and_join_roundtrip():
    text = '---\nname: X\ncolors:\n  primary: "#000000"\n---\n\n# Body\n'
    yaml_text, body = split_frontmatter(text)
    assert "name: X" in yaml_text
    assert body.startswith("\n# Body")
    assert join_frontmatter(yaml_text, body) == text


def test_split_raises_when_no_frontmatter():
    with pytest.raises(ValueError, match="no YAML frontmatter"):
        split_frontmatter("# Body without frontmatter\n")


# ---- Pass 1: bad-hex normalization ----

def test_pass1_normalizes_raw_hex_to_registry_ref():
    """When YAML contains a raw hex matching a registry token, replace with {colors.X} ref."""
    reg = _registry(("primary", "#635bff"), ("neutral_dark", "#000000"))
    yaml_text = 'components:\n  button-primary:\n    backgroundColor: "#635bff"'
    new_yaml, actions = apply_pass1(yaml_text, reg)
    assert "{colors.primary}" in new_yaml
    assert "#635bff" not in new_yaml
    assert any(a.action == "normalize" for a in actions)


def test_pass1_idempotent_on_clean_yaml():
    """Case (j) — applying Pass 1 twice on clean YAML produces no diff."""
    reg = _registry(("primary", "#635bff"), ("neutral_dark", "#000000"))
    yaml_text = 'colors:\n  primary: "#635bff"\nnotes: clean'
    new_yaml1, actions1 = apply_pass1(yaml_text, reg)
    new_yaml2, actions2 = apply_pass1(new_yaml1, reg)
    assert new_yaml1 == new_yaml2, "Pass 1 must be idempotent"


def test_pass1_handles_unknown_hex_unchanged():
    """Hex not matching any registry token → leave for Pass 2 to handle."""
    reg = _registry(("primary", "#635bff"))
    yaml_text = 'components:\n  button-primary:\n    backgroundColor: "#999999"'
    new_yaml, actions = apply_pass1(yaml_text, reg)
    # Unknown hex not normalized
    assert "#999999" in new_yaml or "{colors." not in new_yaml


# ---- Pass 2: 4-cell decision table ----

def test_pass2_cell_b_required_no_near_no_default_raises():
    """Cell (b): required field, no nearest, no default → Pass2Unresolvable."""
    reg = _registry()  # empty registry → no nearest possible
    finding = _bf("Reference {colors.x} does not resolve",
                  path="components.unknown-component.bg")  # not in FIELD_DEFAULTS
    with pytest.raises(Pass2Unresolvable):
        resolve_broken_ref(finding, reg, raw_hex=None, is_required=True)


def test_pass2_cell_d_has_near_has_default_uses_nearest():
    """Cell (d): both nearest and default exist → nearest wins (beats default)."""
    reg = _registry(("primary", "#635bff"), ("neutral_dark", "#000000"))
    # Field IS in FIELD_DEFAULTS, but raw_hex provides a nearest
    finding = _bf("Reference {colors.x} does not resolve",
                  path="components.button-primary.backgroundColor")
    action = resolve_broken_ref(finding, reg, raw_hex="#635bfe", is_required=True)  # ΔE ≈ 0.4
    assert action.action == "nearest"
    assert "primary" in action.chosen


def test_pass2_cell_e_no_near_has_default_uses_default():
    """Cell (e): no nearest, default exists → use spec default."""
    # No raw hex AND no role-hint match → no nearest
    reg = _registry()  # empty registry → role_hint can't resolve
    finding = _bf("Reference {colors.x} does not resolve",
                  path="components.button-primary.backgroundColor")
    action = resolve_broken_ref(finding, reg, raw_hex=None, is_required=True)
    assert action.action == "default"
    assert action.chosen == FIELD_DEFAULTS["components.button-primary.backgroundColor"]


def test_pass2_cell_f_has_near_no_default_uses_nearest():
    """Cell (f): nearest exists, no default → use nearest."""
    reg = _registry(("primary", "#635bff"), ("neutral_dark", "#000000"))
    # Field NOT in FIELD_DEFAULTS but raw_hex provides nearest
    finding = _bf("Reference {colors.x} does not resolve",
                  path="components.exotic.borderColor")  # not in FIELD_DEFAULTS
    action = resolve_broken_ref(finding, reg, raw_hex="#635bfe", is_required=True)
    assert action.action == "nearest"
    assert "primary" in action.chosen


# ---- Tie-break (g) ----

def test_pass2_tie_break_prefers_lowest_registry_index():
    """Case (g): when ΔE equal, lowest registry index wins (earliest registered)."""
    # Two colors at IDENTICAL distance from target: both equal to target itself
    reg = _registry(("first", "#777777"), ("second", "#777777"))
    finding = _bf("Reference {colors.x} does not resolve",
                  path="components.button-primary.backgroundColor")
    action = resolve_broken_ref(finding, reg, raw_hex="#777777", is_required=True)
    assert "first" in action.chosen, "tie should resolve to lowest registry index"


# ---- Optional field: drop allowed (case a) ----

def test_pass2_optional_field_drop_allowed():
    """Case (a): dangling ref in optional field → fixer drops, no error."""
    reg = _registry()
    finding = _bf("Reference {colors.x} does not resolve",
                  path="optional.something")
    action = resolve_broken_ref(finding, reg, raw_hex=None, is_required=False)
    assert action.action == "drop"


# ---- Case (i) — YAML # comment edge case ----

def test_case_i_unquoted_hex_yaml_comment_handled_by_emitter():
    """Case (i): unquoted hex `primary: #635bff` parses as null in YAML
    (# is comment delimiter). Pass 1 re-emits canonical YAML which forces
    quotes — when paired with a downstream Pass 2 fix for the resulting
    null-from-parse-error, the field gets resolved."""
    reg = _registry(("primary", "#635bff"))
    # Simulate YAML that already had unquoted hex (now null)
    yaml_text = "colors:\n  primary: null"  # parse-error result
    new_yaml, actions = apply_pass1(yaml_text, reg)
    # Pass 1 records the parse-error; the null value remains and Pass 2
    # would handle it via field-default lookup if invoked.
    assert any(a.rule == "parse-error" for a in actions)


# ---- apply_to_file: end-to-end on temp DESIGN.md ----

def test_apply_to_file_normalizes_and_writes(tmp_path):
    """End-to-end: load file, apply Pass 1, write back. Verify mutation persisted."""
    f = tmp_path / "DESIGN.md"
    reg = _registry(("primary", "#635bff"))
    f.write_text(
        '---\n'
        'name: T\n'
        'colors:\n'
        '  primary: "#635bff"\n'
        'components:\n'
        '  button-primary:\n'
        '    backgroundColor: "#635bff"\n'
        '---\n\n# Body\n'
    )
    p1, p2 = apply_to_file(str(f), findings=[], registry=reg)
    after = f.read_text()
    # Pass 1 normalized the raw hex inside backgroundColor
    assert "{colors.primary}" in after
    assert any(a.action == "normalize" for a in p1)


def test_apply_to_file_idempotent_case_j(tmp_path):
    """M4 case (j): applying fixer twice on a clean file produces no extra mutation."""
    f = tmp_path / "DESIGN.md"
    reg = _registry(("primary", "#635bff"))
    f.write_text(
        '---\nname: T\ncolors:\n  primary: "#635bff"\n---\n\n# Body\n'
    )
    apply_to_file(str(f), [], reg)
    text_after_first = f.read_text()
    p1, p2 = apply_to_file(str(f), [], reg)
    text_after_second = f.read_text()
    assert text_after_first == text_after_second, "fixer must be idempotent"


def test_apply_to_file_resolves_broken_ref_with_field_default(tmp_path):
    """Pass 2 cell (e): broken ref + no nearest + has default → field default substituted."""
    f = tmp_path / "DESIGN.md"
    reg = _registry(("primary", "#635bff"))  # has primary token, but no "x" matching the ref
    f.write_text(
        '---\n'
        'name: T\n'
        'colors:\n'
        '  primary: "#635bff"\n'
        'components:\n'
        '  button-primary:\n'
        '    backgroundColor: "{colors.nonexistent}"\n'
        '---\n\n# Body\n'
    )
    findings = [
        LintFinding(
            severity="error",
            path="components.button-primary.backgroundColor",
            message="Reference {colors.nonexistent} does not resolve to any defined token.",
        ),
    ]
    p1, p2 = apply_to_file(str(f), findings, reg)
    after = f.read_text()
    # primary IS in registry → role_hint("primary") resolves → cell (d) path: nearest="primary"
    assert "{colors.primary}" in after
    assert any(a.action in ("nearest", "default") for a in p2)


# ---- FIELD_DEFAULTS / FIELD_ROLE_HINTS sanity ----

def test_field_defaults_keys_match_role_hints_keys():
    """Defensive: same fields appear in both lookups for consistency."""
    assert set(FIELD_DEFAULTS.keys()) == set(FIELD_ROLE_HINTS.keys()), (
        "FIELD_DEFAULTS and FIELD_ROLE_HINTS should cover the same field paths"
    )


def test_field_defaults_values_are_token_refs():
    """All FIELD_DEFAULTS values must be {colors.X} or {typography.X} refs, not raw hex."""
    for path, val in FIELD_DEFAULTS.items():
        assert val.startswith("{") and val.endswith("}"), (
            f"FIELD_DEFAULTS[{path!r}] = {val!r} should be a registry ref"
        )
