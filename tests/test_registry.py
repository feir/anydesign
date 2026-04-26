"""Unit tests for Token Registry build, guard, and color-ref resolver (Phase 1.7)."""

from __future__ import annotations

from design_from_url.registry import (
    MIN_COLORS_REQUIRED,
    Registry,
    RegistryGuardError,
    build_registry,
    resolve_color_refs,
)


def _aggregated(colors: list[tuple[str, int]]) -> dict:
    return {
        "colors": [
            {"representative": h, "frequency": f, "members_count": 1, "members": [h]}
            for h, f in colors
        ],
        "spacing": [{"representative_px": 16.0, "frequency": 50, "members": [16.0]}],
        "rounded": [{"representative_px": 4.0, "frequency": 30, "members": [4.0]}],
    }


def _payload_with_root_vars(*hexes: str) -> dict:
    return {
        "root_vars": {f"--c-{i}": h for i, h in enumerate(hexes)},
        "computed_styles": [],
    }


# ---- Registry build (deterministic) ----

def test_build_registry_assigns_color_n_names_in_frequency_order():
    agg = _aggregated([("#635bff", 100), ("#000000", 50), ("#ffffff", 30), ("#ff0000", 10)])
    reg = build_registry(agg, payload={})
    names = [c.name for c in reg.colors]
    assert names == ["color_1", "color_2", "color_3", "color_4"]
    # Frequency carried through.
    assert reg.colors[0].value == "#635bff"
    assert reg.colors[0].frequency == 100


def test_build_registry_canonical_hex_prefers_root_var_member():
    # Cluster top hex is #533afd (most-frequent member), but #635bff also a
    # member AND appears in :root vars → canonical = #635bff.
    agg = {
        "colors": [
            {"representative": "#533afd", "frequency": 200,
             "members": ["#533afd", "#635bff", "#5e58fa"]},
        ],
        "spacing": [], "rounded": [],
    }
    payload = _payload_with_root_vars("#635bff")
    reg = build_registry(agg, payload=payload, min_colors=1)
    assert reg.colors[0].value == "#635bff"


def test_build_registry_falls_back_to_representative_when_no_root_match():
    agg = _aggregated([("#abcdef", 50), ("#111", 30), ("#222", 20)])
    reg = build_registry(agg, payload={"root_vars": {}, "computed_styles": []})
    assert reg.colors[0].value == "#abcdef"


# ---- --primary inject + empty guard ----

def test_guard_aborts_when_under_three_colors_no_primary():
    agg = _aggregated([("#000000", 10), ("#ffffff", 5)])
    try:
        build_registry(agg, payload={})
    except RegistryGuardError as e:
        assert e.registry_color_count == 2
        assert "--primary" in str(e)
        return
    raise AssertionError("guard should have aborted")


def test_primary_inject_synthesizes_first_token_with_role_hint():
    agg = _aggregated([("#000000", 10), ("#ffffff", 5), ("#cccccc", 3)])
    reg = build_registry(agg, payload={}, primary_override="#FF0000")
    first = reg.colors[0]
    assert first.name == "primary"
    assert first.value == "#ff0000"
    assert first.role_hint == "primary"
    assert first.source == "user_override"
    # Subsequent tokens still use color_1, color_2, ... naming.
    assert [c.name for c in reg.colors[1:]] == ["color_1", "color_2", "color_3"]


def test_primary_inject_does_not_double_count_when_color_already_present():
    # Site already has #ff0000 in clusters; --primary inject must dedupe.
    agg = _aggregated([("#ff0000", 50), ("#000000", 30), ("#ffffff", 10)])
    reg = build_registry(agg, payload={}, primary_override="#FF0000")
    values = [c.value for c in reg.colors]
    assert values.count("#ff0000") == 1
    assert reg.colors[0].name == "primary"


def test_guard_message_mentions_primary_when_already_used():
    # Site has 1 color, user provides --primary → 2 colors → still under 3.
    agg = _aggregated([("#000000", 10)])
    try:
        build_registry(agg, payload={}, primary_override="#FF0000")
    except RegistryGuardError as e:
        assert "with --primary" in str(e)
        assert e.registry_color_count == 2
        return
    raise AssertionError("guard should have aborted at 2 colors")


def test_min_colors_can_be_lowered_for_unit_tests():
    agg = _aggregated([("#000000", 10), ("#ffffff", 5)])
    reg = build_registry(agg, payload={}, min_colors=1)
    assert len(reg.colors) == 2


# ---- resolve_color_refs (post-LLM normalizer) ----

def _registry_with(colors: list[tuple[str, str]]) -> Registry:
    """Helper: build a Registry from (name, hex) pairs without going through build_registry."""
    from design_from_url.registry import ColorToken
    tokens = tuple(
        ColorToken(name=n, value=v.lower(), frequency=10, source="extracted",
                   members=(v.lower(),))
        for n, v in colors
    )
    return Registry(
        colors=tokens, typography={}, spacing=(), rounded=(),
        primary_override=None,
    )


def test_resolve_replaces_raw_hex_with_nearest_ref():
    reg = _registry_with([("color_1", "#635bff"), ("color_2", "#000000")])
    yaml = {
        "components": {
            "button-primary": {
                "backgroundColor": "#5e58fa",  # ΔE<6 to color_1
            }
        }
    }
    res = resolve_color_refs(yaml, reg)
    assert res.yaml["components"]["button-primary"]["backgroundColor"] == "{colors.color_1}"
    assert any(a.rule == "raw-hex-to-nearest" for a in res.actions)


def test_resolve_promotes_orphan_hex_when_no_near_neighbor():
    reg = _registry_with([("color_1", "#000000"), ("color_2", "#ffffff")])
    yaml = {"components": {"badge": {"backgroundColor": "#ff00ff"}}}
    res = resolve_color_refs(yaml, reg)
    field = res.yaml["components"]["badge"]["backgroundColor"]
    assert field.startswith("{colors.extra_")
    promoted_names = [c.name for c in res.registry.colors if c.source == "promoted"]
    assert len(promoted_names) == 1


def test_resolve_ref_wins_over_hex_when_both_present():
    reg = _registry_with([("color_1", "#635bff"), ("tertiary", "#abc123")])
    yaml = {
        "components": {
            "card": {
                "borderColor": {"ref": "{colors.tertiary}", "hex": "#ff0000"},
            }
        }
    }
    res = resolve_color_refs(yaml, reg)
    assert res.yaml["components"]["card"]["borderColor"] == "{colors.tertiary}"
    assert any(a.rule == "ref-wins-over-hex" for a in res.actions)


def test_resolve_leaves_existing_refs_untouched():
    reg = _registry_with([("primary", "#635bff"), ("color_1", "#000000")])
    yaml = {"components": {"button-primary": {"backgroundColor": "{colors.primary}"}}}
    res = resolve_color_refs(yaml, reg)
    assert res.yaml["components"]["button-primary"]["backgroundColor"] == "{colors.primary}"
    # No fixer action needed.
    assert len(res.actions) == 0


def test_resolve_walks_nested_dicts_and_lists():
    reg = _registry_with([("color_1", "#000000"), ("color_2", "#ffffff")])
    yaml = {
        "stops": [{"color": "#010101"}, {"color": "#fefefe"}],
        "elevation": {"shadow": {"color": "#000001"}},
    }
    res = resolve_color_refs(yaml, reg)
    assert res.yaml["stops"][0]["color"] == "{colors.color_1}"
    assert res.yaml["stops"][1]["color"] == "{colors.color_2}"
    assert res.yaml["elevation"]["shadow"]["color"] == "{colors.color_1}"
