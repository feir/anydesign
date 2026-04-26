"""DESIGN.md draft assembly.

Composes a YAML front-matter + placeholder-prose markdown file from a
deterministic Token Registry. Vision LLM (Phase 2.3) replaces the prose
placeholders; Schema Fixer (Phase 2.5) normalizes any LLM-introduced raw
hex back to registry refs.

Output schema (best-known approximation of @google/design.md@0.1.1):
- top-level YAML with `name`, `description`, `colors`, `typography`,
  `spacing`, `rounded`, `metadata`
- markdown body with sections: Overview / Components / Do's & Don'ts
- prose blocks marked with `<!-- LLM_PLACEHOLDER:<key> -->` so Phase 2 can
  patch deterministically
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from design_from_url.constants import DESIGN_MD_NPM_VERSION, GENERATOR
from design_from_url.registry import Registry
from design_from_url.yaml_emit import emit_yaml


PLACEHOLDER_OVERVIEW = "<!-- LLM_PLACEHOLDER:overview -->"
PLACEHOLDER_COLORS = "<!-- LLM_PLACEHOLDER:colors_prose -->"
PLACEHOLDER_TYPOGRAPHY = "<!-- LLM_PLACEHOLDER:typography_prose -->"
PLACEHOLDER_LAYOUT = "<!-- LLM_PLACEHOLDER:layout_prose -->"
PLACEHOLDER_COMPONENTS = "<!-- LLM_PLACEHOLDER:components_prose -->"
PLACEHOLDER_DOS = "<!-- LLM_PLACEHOLDER:dos_donts -->"

DEFAULT_NOTES = (
    "Generated for personal reference / internal style transfer.\n"
    "Not authorized for public redistribution."
)

# Standard scale-name ladders by cluster count. spec uses xs/sm/md/lg/xl as
# common conventions (any descriptive string is valid, but these maximize
# downstream tool compatibility — Tailwind/DTCG round-trip).
_SCALE_NAMES_BY_COUNT: dict[int, tuple[str, ...]] = {
    1: ("md",),
    2: ("sm", "md"),
    3: ("sm", "md", "lg"),
    4: ("xs", "sm", "md", "lg"),
    5: ("xs", "sm", "md", "lg", "xl"),
    6: ("xs", "sm", "md", "lg", "xl", "2xl"),
    7: ("2xs", "xs", "sm", "md", "lg", "xl", "2xl"),
}


def _to_dimension(px_value: float) -> str:
    """Convert a numeric px value to a spec-compliant Dimension string."""
    if px_value == int(px_value):
        return f"{int(px_value)}px"
    return f"{px_value:g}px"


def _scale_map(clusters: tuple[dict, ...]) -> dict[str, str]:
    """Convert a list of px clusters to a scale-named map of Dimension strings.

    Clusters arrive frequency-ranked from `aggregate_spacing_and_rounded`,
    but a spacing/rounded scale must be ordered by *size* for tools that
    expect monotonic xs<sm<md<lg<xl.
    """
    if not clusters:
        return {}
    sorted_by_size = sorted(clusters, key=lambda c: c["representative_px"])
    n = len(sorted_by_size)
    names = _SCALE_NAMES_BY_COUNT.get(n) or tuple(f"step_{i+1}" for i in range(n))
    out: dict[str, str] = {}
    for name, c in zip(names, sorted_by_size):
        out[name] = _to_dimension(c["representative_px"])
    return out


def _site_name_from_url(url: str) -> str:
    """Derive a stable, human-friendly name from URL hostname."""
    host = urlparse(url).hostname or url
    # Strip leading 'www.' and reduce to second-level domain for readability.
    host = host.removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return host


def _isoformat_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_yaml_payload(
    registry: Registry,
    *,
    source_url: str,
    site_name: str | None = None,
    description: str | None = None,
    notes: str = DEFAULT_NOTES,
    extracted_at: str | None = None,
    cap_colors: int | None = 12,
) -> dict:
    """Build the YAML-front-matter dict from a Registry. Values only —
    serialization is the YAML emitter's job.

    `cap_colors` caps the colors emitted to the front matter (registry stays
    complete; this only prunes the LLM-facing surface). 12 is a balance —
    spike used 9 candidates and worked; full extraction can yield 80+ and
    drowns the LLM. Pass `None` to disable capping.
    """
    name = site_name or _site_name_from_url(source_url)
    description = description or f"{name} design tokens (auto-extracted)."

    visible = registry.colors[:cap_colors] if cap_colors else registry.colors
    colors = {c.name: c.value for c in visible}

    typography = {}
    for selector, props in registry.typography.items():
        # Spec wants Dimension as string with unit suffix; computed-style
        # values already arrive as "48px"-style strings, so keep them.
        # CSS keyword "normal" (returned by getComputedStyle for
        # unset lineHeight / letterSpacing) is not a valid Dimension —
        # dropping these fields lets lint pass; spec marks them optional.
        clean: dict[str, str] = {}
        for k, v in props.items():
            if not v:
                continue
            if k in ("lineHeight", "letterSpacing") and v == "normal":
                continue
            clean[k] = v
        typography[selector] = clean

    spacing = _scale_map(registry.spacing)
    rounded = _scale_map(registry.rounded)

    return {
        "name": name,
        "description": description,
        "colors": colors,
        "typography": typography,
        "rounded": rounded,
        "spacing": spacing,
    }


def build_metadata_comment(
    *,
    source_url: str,
    extracted_at: str,
    primary_override: str | None,
    notes: str = DEFAULT_NOTES,
) -> str:
    """Compose an HTML comment carrying provenance fields.

    `metadata` is not part of the @google/design.md schema, so we keep our
    extraction provenance in an HTML comment at the top of the markdown
    body instead — invisible to lint but recoverable by Phase 2 LLM and
    human readers.
    """
    lines = [
        "<!-- design-from-url metadata",
        f"source_url: {source_url}",
        f"extracted_at: {extracted_at}",
        f"spec_version: {DESIGN_MD_NPM_VERSION}",
        f"generator: {GENERATOR}",
    ]
    if primary_override:
        lines.append(f"primary_override: {primary_override}")
    lines.append("notes:")
    for note_line in notes.splitlines():
        lines.append(f"  {note_line}")
    lines.append("-->")
    return "\n".join(lines)


def build_markdown_body(
    *,
    site_name: str,
    metadata_comment: str,
) -> str:
    """Build the markdown body following spec section order.

    Order per spec: Overview → Colors → Typography → Layout → Components →
    Do's and Don'ts. Each section starts with a placeholder for Phase 2
    LLM to fill in prose.
    """
    return (
        f"{metadata_comment}\n\n"
        f"# {site_name} — Design Tokens\n\n"
        "## Overview\n\n"
        f"{PLACEHOLDER_OVERVIEW}\n\n"
        "## Colors\n\n"
        f"{PLACEHOLDER_COLORS}\n\n"
        "## Typography\n\n"
        f"{PLACEHOLDER_TYPOGRAPHY}\n\n"
        "## Layout\n\n"
        f"{PLACEHOLDER_LAYOUT}\n\n"
        "## Components\n\n"
        f"{PLACEHOLDER_COMPONENTS}\n\n"
        "## Do's and Don'ts\n\n"
        f"{PLACEHOLDER_DOS}\n"
    )


def build_design_md(
    registry: Registry,
    *,
    source_url: str,
    site_name: str | None = None,
    description: str | None = None,
    notes: str = DEFAULT_NOTES,
    extracted_at: str | None = None,
    cap_colors: int | None = 12,
) -> str:
    """Compose the full DESIGN.md draft (YAML front matter + markdown)."""
    yaml_payload = build_yaml_payload(
        registry,
        source_url=source_url,
        site_name=site_name,
        description=description,
        notes=notes,
        extracted_at=extracted_at,
        cap_colors=cap_colors,
    )
    yaml_text = emit_yaml(yaml_payload)
    name = yaml_payload["name"]
    metadata_comment = build_metadata_comment(
        source_url=source_url,
        extracted_at=extracted_at or _isoformat_now(),
        primary_override=registry.primary_override,
        notes=notes,
    )
    body = build_markdown_body(site_name=name, metadata_comment=metadata_comment)
    return f"---\n{yaml_text}---\n\n{body}"
