"""Unit tests for DESIGN.md template + minimal YAML emitter (Phase 1.8)."""

from __future__ import annotations

from design_from_url.registry import ColorToken, Registry
from design_from_url.template import (
    DEFAULT_NOTES, PLACEHOLDER_COMPONENTS, PLACEHOLDER_DOS, PLACEHOLDER_OVERVIEW,
    build_design_md, build_yaml_payload,
)
from design_from_url.yaml_emit import emit_yaml


def _registry(
    *,
    colors: list[tuple[str, str]] | None = None,
    primary_override: str | None = None,
) -> Registry:
    return Registry(
        colors=tuple(
            ColorToken(name=n, value=v, frequency=10, source="extracted",
                       members=(v,))
            for n, v in (colors or [("color_1", "#635bff"), ("color_2", "#000000"),
                                    ("color_3", "#ffffff")])
        ),
        typography={
            "h1": {
                "fontFamily": "Inter, sans-serif",
                "fontSize": "32px",
                "fontWeight": "700",
                "lineHeight": "40px",
                "letterSpacing": "0px",
            },
            "body": {
                "fontFamily": "Inter, sans-serif",
                "fontSize": "16px",
                "fontWeight": "400",
                "lineHeight": "24px",
                "letterSpacing": "0px",
            },
        },
        spacing=(
            {"representative_px": 4.0, "frequency": 10, "members": [4.0]},
            {"representative_px": 16.0, "frequency": 30, "members": [16.0]},
        ),
        rounded=(
            {"representative_px": 4.0, "frequency": 5, "members": [4.0]},
        ),
        primary_override=primary_override,
    )


# ---- YAML emitter ----

def test_emit_quotes_hex_values():
    out = emit_yaml({"primary": "#635bff"})
    assert '"#635bff"' in out
    # Bare hex would parse as a YAML comment — must not appear unquoted.
    assert "primary: #635bff" not in out


def test_emit_quotes_string_that_looks_like_int():
    out = emit_yaml({"weight": "400"})
    assert '"400"' in out


def test_emit_unquotes_safe_strings():
    out = emit_yaml({"name": "Stripe"})
    assert "name: Stripe" in out


def test_emit_handles_multiline_with_pipe_block():
    out = emit_yaml({"notes": "first line\nsecond line"})
    assert "notes: |" in out
    assert "  first line" in out
    assert "  second line" in out


def test_emit_handles_nested_dicts():
    out = emit_yaml({
        "typography": {
            "h1": {"fontSize": "32px", "fontWeight": "700"},
        },
    })
    # Indentation: 0 / 2 / 4 spaces.
    assert "typography:" in out
    assert "  h1:" in out
    # `32px` is unambiguous, no quoting needed.
    assert "    fontSize: 32px" in out
    # `700` looks like an int, so it must be quoted to stay a string.
    assert '    fontWeight: "700"' in out


def test_emit_handles_list_of_scalars():
    out = emit_yaml({"spacing": [4.0, 8.0, 16.0]})
    assert "spacing:" in out
    assert "- 4.0" in out
    assert "- 16.0" in out


def test_emit_empty_dict_inline():
    out = emit_yaml({"empty": {}})
    assert "empty: {}" in out


def test_emit_round_trip_via_python_yaml_loader_is_optional():
    # If PyYAML is around, emitter output must parse back to the same dict.
    try:
        import yaml  # type: ignore
    except ImportError:
        return
    payload = {
        "name": "Stripe",
        "primary": "#635bff",
        "spacing": [4.0, 16.0],
        "metadata": {
            "source_url": "https://stripe.com",
            "spec_version": "0.1.1",
            "notes": "line1\nline2",
        },
    }
    text = emit_yaml(payload)
    assert yaml.safe_load(text) == payload


# ---- Template assembly ----

def test_build_yaml_payload_omits_metadata_field():
    """Spec doesn't allow `metadata` in YAML; provenance moved to body comment."""
    reg = _registry()
    payload = build_yaml_payload(reg, source_url="https://stripe.com")
    assert "metadata" not in payload


def test_metadata_comment_contains_all_provenance_fields():
    reg = _registry(primary_override="#FF0000")
    out = build_design_md(reg, source_url="https://stripe.com",
                          extracted_at="2026-04-26T00:00:00+00:00")
    assert "<!-- design-from-url metadata" in out
    assert "source_url: https://stripe.com" in out
    assert "extracted_at: 2026-04-26T00:00:00+00:00" in out
    assert "spec_version: 0.1.1" in out
    assert "generator: design-from-url" in out
    assert "primary_override: #FF0000" in out
    assert "-->" in out


def test_metadata_comment_omits_primary_override_when_unset():
    reg = _registry()  # no primary_override
    out = build_design_md(reg, source_url="https://x.com")
    assert "primary_override" not in out


# ---- Phase 2.0 — CLI --primary contract end-to-end ----

def test_cli_primary_flows_to_yaml_colors_primary():
    """When --primary "#FF0000" is passed, YAML frontmatter colors.primary
    appears as lowercase quoted hex (registry canonical-hex normalization).
    The original-case input is preserved in metadata.primary_override."""
    from design_from_url.registry import build_registry
    agg = {
        "spacing": (), "rounded": (),
        "colors": [
            {"representative": "#000000", "frequency": 50, "members": ["#000000"]},
            {"representative": "#ffffff", "frequency": 30, "members": ["#ffffff"]},
        ],
    }
    reg = build_registry(agg, payload={}, primary_override="#FF0000")
    out = build_design_md(reg, source_url="https://x.com",
                          extracted_at="2026-04-26T00:00:00+00:00")
    # YAML field — registry normalizes to lowercase canonical form
    assert 'primary: "#ff0000"' in out
    # Metadata HTML comment — preserves original input case for traceability
    assert "primary_override: #FF0000" in out


def test_cli_primary_distinct_from_yaml_metadata_field():
    """The metadata.primary_override is NOT in YAML frontmatter (spec rejects it),
    only in the HTML comment block in body — Phase 1.9 spec finding."""
    from design_from_url.registry import build_registry
    agg = {
        "spacing": (), "rounded": (),
        "colors": [
            {"representative": "#000000", "frequency": 50, "members": ["#000000"]},
            {"representative": "#ffffff", "frequency": 30, "members": ["#ffffff"]},
        ],
    }
    reg = build_registry(agg, payload={}, primary_override="#abc123")
    out = build_design_md(reg, source_url="https://x.com")
    # Split YAML vs body
    yaml_block, _, body = out.partition("\n---\n")  # first --- is open, second is close
    assert "primary_override" not in yaml_block, (
        "primary_override leaked into YAML frontmatter — spec rejects this field"
    )
    assert "primary_override: #abc123" in body


def test_build_design_md_full_output_has_yaml_frontmatter_and_placeholders():
    reg = _registry()
    out = build_design_md(reg, source_url="https://stripe.com",
                          extracted_at="2026-04-26T00:00:00+00:00")
    # YAML front matter delimiters.
    assert out.startswith("---\n")
    assert "\n---\n" in out
    # Placeholders for Phase 2 LLM patching.
    assert PLACEHOLDER_OVERVIEW in out
    assert PLACEHOLDER_COMPONENTS in out
    assert PLACEHOLDER_DOS in out
    # Hex must be quoted (else YAML parses as comment).
    assert '"#635bff"' in out


def test_build_design_md_derives_site_name_from_hostname():
    reg = _registry()
    out = build_design_md(reg, source_url="https://www.stripe.com/")
    assert "Stripe" in out


def test_build_yaml_payload_colors_dict_keyed_by_token_name():
    reg = _registry(colors=[("primary", "#ff0000"), ("color_1", "#000000"),
                            ("color_2", "#ffffff")],
                    primary_override="#FF0000")
    payload = build_yaml_payload(reg, source_url="https://x.com")
    assert list(payload["colors"].keys()) == ["primary", "color_1", "color_2"]
    assert payload["colors"]["primary"] == "#ff0000"


def test_build_yaml_payload_omits_typography_props_with_empty_values():
    reg = Registry(
        colors=tuple(_registry().colors),
        typography={"h1": {"fontFamily": "Inter", "fontSize": "32px",
                          "fontWeight": "", "lineHeight": "", "letterSpacing": ""}},
        spacing=(), rounded=(),
    )
    payload = build_yaml_payload(reg, source_url="https://x.com")
    h1 = payload["typography"]["h1"]
    assert "fontFamily" in h1 and "fontSize" in h1
    assert "fontWeight" not in h1
    assert "lineHeight" not in h1
