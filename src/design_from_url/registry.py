"""Token Registry — pre-LLM deterministic token candidate set.

Layers:
1. `build_registry(aggregated, primary_override?)` constructs the ordered
   candidate list from aggregated colors + length clusters + typography.
2. `--primary` injection runs FIRST so the empty-guard sees the synthetic
   entry (matters when site has only 2 native colors but user knows brand).
3. Empty guard aborts when colors < `MIN_COLORS_REQUIRED` (3 by default),
   raising `RegistryGuardError` with an actionable message.
4. `resolve_color_refs(payload, registry)` is the post-LLM normalizer:
   replaces raw hex with `{colors.X}` refs (ΔE<6), drops hex when ref+hex
   coexist in the same field, and promotes new colors to `extra_<n>` when
   ΔE>6 and a real component references them.

Registry naming uses spike's `color_1`, `color_2`, ... convention plus an
optional `primary` slot for `--primary` overrides; semantic role names
(`secondary`, `tertiary`, ...) are LLM responsibility downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from design_from_url.colors import (
    ColorCluster, RGBA, parse_color, srgb_to_lab, delta_e76,
)


MIN_COLORS_REQUIRED = 3


class RegistryGuardError(RuntimeError):
    """Raised when the registry has insufficient design signal to proceed."""

    def __init__(self, message: str, *, registry_color_count: int):
        super().__init__(message)
        self.registry_color_count = registry_color_count


@dataclass(frozen=True)
class ColorToken:
    name: str           # e.g. "color_1" / "primary" / "extra_3"
    value: str          # canonical hex like "#635bff"
    frequency: int
    role_hint: str | None = None    # e.g. "primary" if --primary inject
    source: str = "extracted"       # "extracted" | "user_override" | "promoted"
    members: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Registry:
    colors: tuple[ColorToken, ...]
    typography: dict        # {selector: {fontFamily, fontSize, fontWeight, ...}}
    spacing: tuple[dict, ...]   # cluster dicts {representative_px, frequency, members}
    rounded: tuple[dict, ...]
    primary_override: str | None = None   # the hex passed via --primary, if any

    def to_json(self) -> dict:
        return {
            "colors": [
                {
                    "name": c.name,
                    "value": c.value,
                    "frequency": c.frequency,
                    "role_hint": c.role_hint,
                    "source": c.source,
                    "members_count": len(c.members),
                }
                for c in self.colors
            ],
            "typography": self.typography,
            "spacing": list(self.spacing),
            "rounded": list(self.rounded),
            "primary_override": self.primary_override,
        }


# ---- Canonical hex selection ----

def _select_canonical_hex(
    cluster: ColorCluster,
    root_var_hex_set: frozenset[str],
) -> str:
    """Pick the cluster's canonical hex.

    Priority: any member that appears in `:root` vars wins (designers' explicit
    declaration is more authoritative than computed-style frequency); otherwise
    fall back to the cluster's own representative (most-frequent member).
    """
    for h in cluster.members:
        if h.lower() in root_var_hex_set:
            return h.lower()
    return cluster.representative


def _root_var_hex_set(payload: dict) -> frozenset[str]:
    """Build a set of normalized hex strings appearing in `:root` var values."""
    out: set[str] = set()
    for v in (payload.get("root_vars") or {}).values():
        if not isinstance(v, str):
            continue
        rgba = parse_color(v.strip())
        if rgba is None:
            continue
        out.add(rgba.to_hex())
    return frozenset(out)


# ---- Typography candidates ----

def _typography_from_payload(payload: dict) -> dict:
    """Pick one representative sample per typographic selector.

    Only headings + body + interactive elements that carry meaningful font
    metadata; computed_styles already gives us up-to-5 samples per selector,
    we keep the first one that has a non-default font-family.
    """
    interesting = ("h1", "h2", "h3", "body", "button", "a", "input")
    by_selector: dict[str, dict] = {}
    for sample in payload.get("computed_styles", []):
        sel = sample.get("selector")
        if sel not in interesting or sel in by_selector:
            continue
        if sample.get("_is_default_family"):
            continue
        by_selector[sel] = {
            "fontFamily": sample.get("font-family", ""),
            "fontSize": sample.get("font-size", ""),
            "fontWeight": sample.get("font-weight", ""),
            "lineHeight": sample.get("line-height", ""),
            "letterSpacing": sample.get("letter-spacing", ""),
        }
    return by_selector


# ---- Registry build ----

def build_registry(
    aggregated: dict,
    payload: dict,
    *,
    primary_override: str | None = None,
    min_colors: int = MIN_COLORS_REQUIRED,
) -> Registry:
    """Build a Registry from aggregator output + raw payload.

    `aggregated` is a dict like the one `aggregate_spacing_and_rounded` +
    `dedupe_colors` produce together (or whatever the CLI `aggregate`
    subcommand emits); `payload` is the raw extractor output (needed for
    typography + canonical-hex source priority).

    Order of operations (matters for the guard):
      1. Build color tokens from aggregated clusters + canonical-hex pick.
      2. If `primary_override` provided, prepend a synthetic `primary` token.
      3. Evaluate guard against the resulting count; raise on insufficient
         signal (with hint about `--primary` if no override was used).
    """
    color_clusters = aggregated.get("color_clusters") or aggregated.get("colors") or []
    root_hex_set = _root_var_hex_set(payload)

    color_tokens: list[ColorToken] = []
    used_canonicals: set[str] = set()

    if primary_override:
        primary_norm = primary_override.lower()
        if not primary_norm.startswith("#"):
            primary_norm = "#" + primary_norm
        color_tokens.append(ColorToken(
            name="primary",
            value=primary_norm,
            frequency=1,
            role_hint="primary",
            source="user_override",
            members=(primary_norm,),
        ))
        used_canonicals.add(primary_norm)

    # Order matters: cluster ordering (already frequency-desc from dedupe)
    # determines color_<n> indices, which in turn drive LLM candidate visibility.
    extracted_count = 0
    for cluster_obj in color_clusters:
        # Accept both `ColorCluster` instances and dict shapes (CLI hands JSON).
        if isinstance(cluster_obj, ColorCluster):
            cluster = cluster_obj
        else:
            cluster = ColorCluster(
                representative=cluster_obj["representative"],
                frequency=int(cluster_obj["frequency"]),
                members=tuple(cluster_obj.get("members", [cluster_obj["representative"]])),
            )
        canonical = _select_canonical_hex(cluster, root_hex_set)
        if canonical in used_canonicals:
            # Skip duplicate — primary override already covers this color.
            continue
        used_canonicals.add(canonical)
        extracted_count += 1
        color_tokens.append(ColorToken(
            name=f"color_{extracted_count}",
            value=canonical,
            frequency=cluster.frequency,
            role_hint=None,
            source="extracted",
            members=cluster.members,
        ))

    typography = _typography_from_payload(payload)
    spacing = tuple(aggregated.get("spacing") or [])
    rounded = tuple(aggregated.get("rounded") or [])

    registry = Registry(
        colors=tuple(color_tokens),
        typography=typography,
        spacing=spacing,
        rounded=rounded,
        primary_override=primary_override,
    )

    # Empty guard — evaluated AFTER injection so --primary can move us above
    # the bar in marginal cases.
    if len(registry.colors) < min_colors:
        if primary_override:
            msg = (
                f"with --primary the registry has {len(registry.colors)} "
                f"colors but minimum {min_colors} required — site has "
                f"insufficient design signal even with manual brand override"
            )
        else:
            msg = (
                f"site has insufficient design signal "
                f"({len(registry.colors)} colors, {min_colors} required) — "
                f"try `--primary <hex>` to manually inject brand color"
            )
        raise RegistryGuardError(msg, registry_color_count=len(registry.colors))

    return registry


# ---- LLM-output color-ref resolver (Phase 2.5 hook) ----

@dataclass(frozen=True)
class ResolveAction:
    rule: str       # "ref-wins-over-hex" / "raw-hex-to-nearest" / "promoted-extra"
    target: str     # path or token name affected
    detail: str = ""


@dataclass(frozen=True)
class ResolveResult:
    yaml: dict
    registry: "Registry"
    actions: tuple[ResolveAction, ...]


def _to_lab(rgba: RGBA) -> tuple[float, float, float]:
    return srgb_to_lab(rgba)


def resolve_color_refs(
    yaml_dict: dict,
    registry: Registry,
    *,
    delta_e_threshold: float = 6.0,
) -> ResolveResult:
    """Walk a YAML-shaped dict and normalize color fields against `registry`.

    Resolution rules (Pass 1 — design.md D6 mixed-mode normalization):
      - same-field `ref + hex` co-existence → drop hex, keep ref
      - lone raw `#hex` → resolve to nearest registry token (ΔE<6); if no
        match within threshold and a component truly references it, promote
        as new `extra_<n>` and add to registry
      - ref `{colors.X}` for unknown X → leave intact (Pass 2 handles it)

    Returns the rewritten dict + a fixer-action log for run_report.json.
    """
    actions: list[ResolveAction] = []
    # Build a lookup from token name → ColorToken (for ref validation).
    by_name: dict[str, ColorToken] = {c.name: c for c in registry.colors}
    # Pre-compute Lab values for all registry colors (for nearest-hex search).
    reg_labs: list[tuple[ColorToken, tuple[float, float, float]]] = []
    for c in registry.colors:
        rgba = parse_color(c.value)
        if rgba is None:
            continue
        reg_labs.append((c, _to_lab(rgba)))

    promoted: list[ColorToken] = []
    next_extra_idx = 1 + sum(1 for c in registry.colors if c.name.startswith("extra_"))

    def _is_ref(s: str) -> bool:
        return isinstance(s, str) and s.startswith("{colors.") and s.endswith("}")

    def _ref_name(s: str) -> str:
        return s[len("{colors."):-1]

    def _is_hex(s: str) -> bool:
        return isinstance(s, str) and s.startswith("#") and len(s) in (4, 7, 9)

    def _resolve_hex_to_ref(hex_value: str, field_path: str) -> str:
        nonlocal next_extra_idx
        rgba = parse_color(hex_value)
        if rgba is None:
            return hex_value
        target_lab = _to_lab(rgba)
        best, best_d = None, float("inf")
        for token, lab in reg_labs:
            d = delta_e76(target_lab, lab)
            if d < best_d:
                best, best_d = token, d
        if best and best_d < delta_e_threshold:
            actions.append(ResolveAction(
                rule="raw-hex-to-nearest",
                target=field_path,
                detail=f"{hex_value} → {{colors.{best.name}}} (ΔE={best_d:.2f})",
            ))
            return f"{{colors.{best.name}}}"
        # Promote: only when a real component references this hex (otherwise
        # leave it; aggregator's orphan-tokens warning would fire).
        new_name = f"extra_{next_extra_idx}"
        next_extra_idx += 1
        new_token = ColorToken(
            name=new_name, value=hex_value.lower(), frequency=1,
            role_hint=None, source="promoted",
            members=(hex_value.lower(),),
        )
        promoted.append(new_token)
        reg_labs.append((new_token, target_lab))
        actions.append(ResolveAction(
            rule="promoted-extra",
            target=field_path,
            detail=f"{hex_value} → {{colors.{new_name}}}",
        ))
        return f"{{colors.{new_name}}}"

    def _normalize_value(v, path: str):
        # Detect mixed mode: dict with both "ref" and "hex" keys (unusual but
        # plan v5 D6 mentions it). For pure scalars, decide ref vs hex.
        if isinstance(v, dict) and "ref" in v and "hex" in v:
            actions.append(ResolveAction(
                rule="ref-wins-over-hex", target=path,
                detail=f"dropped hex={v['hex']!r}, kept ref={v['ref']!r}",
            ))
            return v["ref"]
        if _is_hex(v):
            return _resolve_hex_to_ref(v.lower(), path)
        if isinstance(v, dict):
            return {k: _normalize_value(vv, f"{path}.{k}") for k, vv in v.items()}
        if isinstance(v, list):
            return [_normalize_value(vv, f"{path}[{i}]") for i, vv in enumerate(v)]
        return v

    out = {k: _normalize_value(v, k) for k, v in yaml_dict.items()}

    if promoted:
        new_registry = Registry(
            colors=tuple(list(registry.colors) + promoted),
            typography=registry.typography,
            spacing=registry.spacing,
            rounded=registry.rounded,
            primary_override=registry.primary_override,
        )
    else:
        new_registry = registry

    return ResolveResult(
        yaml=out, registry=new_registry, actions=tuple(actions),
    )
