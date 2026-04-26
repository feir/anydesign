"""Schema Fixer — Phase 2.5 deterministic post-LLM normalization.

Two passes (per design.md D3):

**Pass 1 (mixed normalization)** — applies to YAML front-matter values:
- Bad hex value (e.g. `colors.primary: "#xyz"`) → look up canonical from
  registry member set or fail fast
- parse-error (unquoted hex → null) → re-emit canonical YAML with quoted hex

**Pass 2 (4-cell required-field decision table)** — for `broken-ref` errors
in components.X (or any other required field):

| Cell | has_nearest_ΔE<10 | has_default | Resolution |
|------|-------------------|-------------|------------|
| (b)  | No                | No          | RAISE Pass2Unresolvable (caller writes WARN+exit 2) |
| (d)  | Yes               | Yes         | nearest (nearest beats default) |
| (e)  | No                | Yes         | default |
| (f)  | Yes               | No          | nearest |

Tie-break (g): when ΔE equal, prefer lower registry index (earliest registered).

Optional fields: nearest → drop allowed.

YAML round-trip strategy: split on `\\n---\\n` boundary (template emits
`---\\n<yaml>---\\n\\n<body>`), modify only the YAML half via parse →
mutate → re-emit, paste body back unchanged.

NOTE on Pass 1 scope (advisor input post lint-surface discovery):
real lint v0.1.1 only flags 3 errors — bad-hex in YAML value, broken-ref
in components, parse-error (null after unquoted hex). The original
"raw hex in markdown body → ref" path was dropped because lint doesn't
detect it; touching prose body would mutate without benefit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from design_from_url.colors import delta_e76, parse_color, srgb_to_lab
from design_from_url.preflight import LintFinding
from design_from_url.registry import ColorToken, Registry


# Hardcoded spec defaults for required fields. Per advisor option (b):
# pragmatic — only the fields we know lint rejects when missing.
# If @google/design.md@0.2 adds new required fields, extend this dict.
FIELD_DEFAULTS: dict[str, str] = {
    "components.button-primary.backgroundColor": "{colors.primary}",
    "components.button-primary.color": "{colors.neutral_light}",
    "components.button-secondary.backgroundColor": "{colors.neutral_light}",
    "components.button-secondary.color": "{colors.neutral_dark}",
    "components.card.backgroundColor": "{colors.neutral_light}",
}

# Field-path → role hint mapping. Used by Pass 2 when the broken ref's
# original raw hex isn't available — we lean on the field's semantic role
# to pick a registry token.
FIELD_ROLE_HINTS: dict[str, str] = {
    "components.button-primary.backgroundColor": "primary",
    "components.button-primary.color": "neutral_light",
    "components.button-secondary.backgroundColor": "neutral_light",
    "components.button-secondary.color": "neutral_dark",
    "components.card.backgroundColor": "neutral_light",
}

NEAREST_DELTA_E_GATE = 10.0  # Pass 2 considers a token "near" if ΔE76 < 10


class Pass2Unresolvable(Exception):
    """Raised by Pass 2 when a required field has no nearest registry color
    AND no spec default — terminal failure (cell (b) of decision table)."""

    def __init__(self, target: str, message: str = ""):
        self.target = target
        super().__init__(
            f"required field {target!r} has no nearest registry color "
            f"and no spec default: {message}"
        )


# ---- YAML round-trip ----

_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split DESIGN.md into (yaml_text, body). Raises if no frontmatter."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError("DESIGN.md has no YAML frontmatter (expected leading '---')")
    return m.group(1), text[m.end():]


def join_frontmatter(yaml_text: str, body: str) -> str:
    """Inverse of split_frontmatter: rebuild DESIGN.md from yaml + body."""
    return f"---\n{yaml_text}\n---\n{body}"


# ---- Pass 1: mixed normalization ----

@dataclass(frozen=True)
class Pass1Action:
    """Record of a single mutation by Pass 1, for run_report.fixer_actions."""
    rule: str       # "bad-hex" | "parse-error"
    action: str     # "normalize" | "re-emit"
    target: str     # field path (e.g. "colors.primary")


def _walk_yaml_for_hex_repairs(
    yaml_obj: dict, registry: Registry, *, path: str = "",
) -> list[Pass1Action]:
    """Recursively walk YAML dict, normalizing bad/raw hex values.

    Mutates yaml_obj in place. Returns list of actions taken.
    """
    actions: list[Pass1Action] = []
    if not isinstance(yaml_obj, dict):
        return actions
    for key, val in list(yaml_obj.items()):
        cur_path = f"{path}.{key}" if path else key
        if isinstance(val, dict):
            actions.extend(_walk_yaml_for_hex_repairs(val, registry, path=cur_path))
        elif isinstance(val, str) and val.startswith("#"):
            # Raw hex in YAML value. Try to find a registry color whose
            # canonical or member matches; otherwise leave as-is (Pass 2
            # may handle if it's a broken-ref).
            hex_lower = val.lower()
            for tok in registry.colors:
                if tok.value.lower() == hex_lower or hex_lower in {m.lower() for m in tok.members}:
                    yaml_obj[key] = f"{{colors.{tok.name}}}"
                    actions.append(Pass1Action(
                        rule="raw-hex", action="normalize", target=cur_path,
                    ))
                    break
        elif val is None and key in ("primary", "neutral_dark", "neutral_light"):
            # parse-error case: unquoted hex in YAML parsed as null
            # (YAML treated `#xxxxxx` as start-of-comment). We can't
            # recover the original hex from a null; let Pass 2 fall back
            # to default for required fields, or mark for drop on optional.
            actions.append(Pass1Action(
                rule="parse-error", action="needs-pass2", target=cur_path,
            ))
    return actions


def apply_pass1(yaml_text: str, registry: Registry) -> tuple[str, list[Pass1Action]]:
    """Parse YAML, normalize raw hex → registry refs, re-emit canonical YAML.

    Returns (new_yaml_text, actions_taken). The re-emit step itself fixes
    parse-errors caused by unquoted hex (via the YAML emitter's quoting rules).
    """
    from design_from_url.yaml_emit import emit_yaml

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        # Catastrophic parse failure — return original text + error action
        return yaml_text, [Pass1Action(
            rule="parse-error", action="parse-failed",
            target=f"yaml: {exc}",
        )]

    if not isinstance(data, dict):
        return yaml_text, []

    actions = _walk_yaml_for_hex_repairs(data, registry)
    return emit_yaml(data), actions


# ---- Pass 2: 4-cell required-field decision table ----

@dataclass(frozen=True)
class Pass2Action:
    rule: str       # "broken-ref"
    action: str     # "nearest" | "default" | "drop"
    target: str     # field path
    chosen: str     # the token name or default value chosen


def _hex_nearest_in_registry(
    hex_value: str, registry: Registry,
) -> tuple[ColorToken, float] | None:
    """Find registry token with smallest ΔE76 from hex_value.

    Returns (token, delta_e) or None if registry is empty or hex unparseable.
    Tie-break (g): when multiple tokens have equal min ΔE, returns the one
    with the lowest registry index (earliest registered).
    """
    rgba = parse_color(hex_value)
    if rgba is None or not registry.colors:
        return None
    target_lab = srgb_to_lab(rgba)
    best_idx = -1
    best_delta = float("inf")
    for idx, tok in enumerate(registry.colors):
        tok_rgba = parse_color(tok.value)
        if tok_rgba is None:
            continue
        tok_lab = srgb_to_lab(tok_rgba)
        d = delta_e76(target_lab, tok_lab)
        if d < best_delta:
            best_delta = d
            best_idx = idx
    if best_idx < 0:
        return None
    return registry.colors[best_idx], best_delta


def _resolve_role_hint_to_token(role: str, registry: Registry) -> ColorToken | None:
    """Look up a registry token by role hint. First match by name == role,
    then by token.role_hint == role."""
    for tok in registry.colors:
        if tok.name == role:
            return tok
    for tok in registry.colors:
        if getattr(tok, "role_hint", None) == role:
            return tok
    return None


def resolve_broken_ref(
    finding: LintFinding,
    registry: Registry,
    *,
    raw_hex: str | None = None,
    is_required: bool = True,
) -> Pass2Action:
    """Apply the 4-cell decision table to a single broken-ref finding.

    Args:
        finding: A LintFinding with severity="error" and message containing
            "Reference {colors.X} does not resolve".
        registry: Token registry.
        raw_hex: Optional pre-extracted raw hex from the offending field
            (Pass 1 surface). When None, falls back to FIELD_ROLE_HINTS lookup.
        is_required: True for required fields (4-cell logic); False allows
            "drop" as a valid optional-field resolution.

    Returns:
        Pass2Action recording what was chosen.

    Raises:
        Pass2Unresolvable: cell (b) — required + no nearest + no default.
    """
    target = finding.path

    # Determine input source for nearest-by-ΔE search
    nearest = None
    if raw_hex:
        nearest = _hex_nearest_in_registry(raw_hex, registry)
    else:
        # No raw hex available — try role_hint fallback before giving up on nearest
        role = FIELD_ROLE_HINTS.get(target)
        if role:
            tok = _resolve_role_hint_to_token(role, registry)
            if tok:
                nearest = (tok, 0.0)  # role match is exact for our purposes

    has_nearest = nearest is not None and nearest[1] < NEAREST_DELTA_E_GATE
    default = FIELD_DEFAULTS.get(target)
    has_default = default is not None

    # 4-cell decision table
    if has_nearest:
        # Cells (d) and (f): nearest wins (beats default by spec)
        tok, _delta = nearest  # type: ignore[misc]
        return Pass2Action(
            rule="broken-ref", action="nearest",
            target=target, chosen=f"{{colors.{tok.name}}}",
        )
    if has_default:
        # Cell (e): no nearest, fall back to spec default
        return Pass2Action(
            rule="broken-ref", action="default",
            target=target, chosen=default,
        )
    if not is_required:
        # Optional field: drop is a valid resolution
        return Pass2Action(
            rule="broken-ref", action="drop",
            target=target, chosen="",
        )
    # Cell (b): required + no nearest + no default → terminal failure
    raise Pass2Unresolvable(target, message=finding.message)


# ---- Public orchestration entry ----

def apply_to_file(
    file_path: str, findings: list[LintFinding], registry: Registry,
) -> tuple[list[Pass1Action], list[Pass2Action]]:
    """Apply Pass 1 + Pass 2 to a DESIGN.md file in place.

    Pass 1 always runs (cheap, idempotent on clean files).
    Pass 2 only triggers if there are broken-ref findings.

    Returns (pass1_actions, pass2_actions). Caller (cli.py) folds these
    into run_report.fixer_actions and decides whether to re-lint.

    Raises:
        Pass2Unresolvable: when a required broken-ref can't be resolved.
            cli.py must catch and translate to WARN+exit 2 + run_report.
    """
    with open(file_path, encoding="utf-8") as f:
        text = f.read()
    yaml_text, body = split_frontmatter(text)

    new_yaml, p1_actions = apply_pass1(yaml_text, registry)

    # Pass 2: process broken-ref errors
    p2_actions: list[Pass2Action] = []
    broken_refs = [
        f for f in findings
        if f.severity == "error" and "Reference" in f.message and "does not resolve" in f.message
    ]
    if broken_refs:
        # Re-parse the (now Pass-1-normalized) YAML so we can rewrite refs
        data = yaml.safe_load(new_yaml)
        for finding in broken_refs:
            # Field path determines required vs optional
            is_required = finding.path in FIELD_DEFAULTS
            action = resolve_broken_ref(finding, registry, is_required=is_required)
            _set_yaml_path(data, finding.path, action.chosen if action.action != "drop" else None)
            p2_actions.append(action)
        from design_from_url.yaml_emit import emit_yaml
        new_yaml = emit_yaml(data)

    new_text = join_frontmatter(new_yaml, body)
    if new_text != text:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    return p1_actions, p2_actions


def _set_yaml_path(data: dict, path: str, value: str | None) -> None:
    """Walk a dotted path into nested dicts and set/delete the leaf.

    `value=None` deletes the leaf; otherwise assigns it.
    Silently no-ops if any path segment is missing (the field may have been
    dropped by an earlier pass).
    """
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if not isinstance(cur, dict):
        return
    leaf = parts[-1]
    if value is None:
        cur.pop(leaf, None)
    else:
        cur[leaf] = value
