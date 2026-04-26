"""Minimal safe-by-default YAML emitter (no external deps).

Handles only what `design-from-url` needs:
- top-level dict with scalar / nested-dict / list values
- list of scalars (rendered as `- value`)
- list of dicts (rendered as `- key: value` blocks)
- multiline strings via `|` block scalar
- ALWAYS quotes hex literals (leading `#` is YAML comment delimiter; quoting
  is the only safe path — see design.md D-finding from Phase 0 spike).

Output is intentionally close to PyYAML default-flow-style=False for
readability + downstream tool compatibility.
"""

from __future__ import annotations

import re
from typing import Any

# Strings that need quoting because they would otherwise parse as something else.
_BOOL_KEYWORDS = frozenset({
    "true", "false", "null", "yes", "no", "on", "off", "~", "",
})
_INT_OR_FLOAT_RE = re.compile(r"^-?(\d+|\d*\.\d+)([eE][-+]?\d+)?$")
_NEEDS_QUOTE_CHARS = re.compile(r"[\:\[\]\{\},&\*\?\|<>=!%@`]")


def _quote_str(s: str) -> str:
    """Return YAML representation of a string, quoting only when necessary."""
    if s == "":
        return '""'
    # Multiline content is handled by caller using `|` scalar.
    lower = s.lower()
    needs_quote = (
        s.startswith("#")
        or s.startswith(" ") or s.endswith(" ")
        or "\n" in s
        or lower in _BOOL_KEYWORDS
        or _INT_OR_FLOAT_RE.match(s)
        or _NEEDS_QUOTE_CHARS.search(s) is not None
    )
    if not needs_quote:
        return s
    # Use double quotes; escape any embedded `"` and `\`.
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _emit_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return _quote_str(v)
    raise TypeError(f"Unsupported scalar type: {type(v)}")


def _is_multiline(s: str) -> bool:
    return isinstance(s, str) and "\n" in s


def _emit_multiline(s: str, indent: int) -> list[str]:
    pad = " " * indent
    lines = ["|"]
    for line in s.splitlines():
        lines.append(f"{pad}{line}")
    return lines


def emit_yaml(data: dict, *, indent_step: int = 2) -> str:
    """Serialize a dict to YAML text. Returns a string ending in newline."""
    out: list[str] = []
    _emit_mapping(data, out, level=0, indent_step=indent_step)
    return "\n".join(out) + "\n"


def _emit_mapping(d: dict, out: list[str], *, level: int, indent_step: int) -> None:
    pad = " " * (level * indent_step)
    for k, v in d.items():
        key = _quote_str(str(k))
        if isinstance(v, dict):
            if not v:
                out.append(f"{pad}{key}: {{}}")
            else:
                out.append(f"{pad}{key}:")
                _emit_mapping(v, out, level=level + 1, indent_step=indent_step)
        elif isinstance(v, list):
            if not v:
                out.append(f"{pad}{key}: []")
            else:
                out.append(f"{pad}{key}:")
                _emit_list(v, out, level=level + 1, indent_step=indent_step)
        elif _is_multiline(v):
            out.append(f"{pad}{key}: |")
            _emit_multiline_lines(v, out, level=level + 1, indent_step=indent_step)
        else:
            out.append(f"{pad}{key}: {_emit_scalar(v)}")


def _emit_list(items: list, out: list[str], *, level: int, indent_step: int) -> None:
    pad = " " * (level * indent_step)
    for item in items:
        if isinstance(item, dict):
            if not item:
                out.append(f"{pad}- {{}}")
                continue
            keys = list(item.keys())
            first_key = keys[0]
            first_val = item[first_key]
            # Render first kv on the dash line, rest indented under it.
            if isinstance(first_val, (dict, list)) or _is_multiline(first_val):
                out.append(f"{pad}-")
                _emit_mapping(item, out, level=level + 1, indent_step=indent_step)
            else:
                out.append(f"{pad}- {_quote_str(str(first_key))}: {_emit_scalar(first_val)}")
                if len(keys) > 1:
                    sub = {k: item[k] for k in keys[1:]}
                    _emit_mapping(sub, out, level=level + 1, indent_step=indent_step)
        elif isinstance(item, list):
            out.append(f"{pad}-")
            _emit_list(item, out, level=level + 1, indent_step=indent_step)
        elif _is_multiline(item):
            out.append(f"{pad}- |")
            _emit_multiline_lines(item, out, level=level + 1, indent_step=indent_step)
        else:
            out.append(f"{pad}- {_emit_scalar(item)}")


def _emit_multiline_lines(s: str, out: list[str], *, level: int, indent_step: int) -> None:
    pad = " " * (level * indent_step)
    for line in s.splitlines() or [""]:
        out.append(f"{pad}{line}")
