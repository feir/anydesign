"""Prompt template loader + post-processor for Phase 2.3.

CRITICAL design choice (plan-review C2): templates use `<<key>>` substitution
via `str.replace`, NOT Python's `str.format`. Reason: `str.format` parses
`{colors.primary}` as a format field (attribute access on a `colors` kwarg),
crashing on the very first prompt load. The prompts MUST contain literal
`{colors.X}` token references for the LLM to use; `<<key>>` syntax leaves
those literals untouched.

`HEX_PATTERN` covers 3/6/8-digit forms (e.g. `#fff`, `#635bff`, `#635bff80`).
`extract_hex_from_yaml_value` is restricted to YAML front-matter scope so
prose code fences and inline examples in markdown body aren't rewritten.
"""

from __future__ import annotations

import re
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"


# Matches 3-, 6-, or 8-digit hex (with optional alpha channel).
# Anchored at `#` to avoid false positives like `abc123` on its own.
HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)?\b")


class PromptNotFoundError(LookupError):
    """Raised when a prompt name doesn't map to a `prompts/<name>.md` file."""


def load_prompt(name: str, **kwargs: str) -> str:
    """Load `prompts/<name>.md` and substitute `<<key>>` placeholders.

    Args:
        name: Filename stem (e.g. "overview" loads `prompts/overview.md`).
        **kwargs: Substitutions; each `key=value` replaces all `<<key>>`
            occurrences with `value`.

    Returns:
        Prompt text with substitutions applied. Literal `{colors.X}` and
        other `{...}` patterns are preserved verbatim.

    Raises:
        PromptNotFoundError: When `prompts/<name>.md` does not exist.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptNotFoundError(
            f"prompt {name!r} not found at {path} "
            f"(expected one of {[p.stem for p in PROMPTS_DIR.glob('*.md')]})"
        )
    text = path.read_text(encoding="utf-8")
    for key, val in kwargs.items():
        text = text.replace(f"<<{key}>>", str(val))
    return text


def extract_hex_from_yaml_value(text: str) -> list[str]:
    """Extract all hex color codes from a string.

    Designed for use on YAML front-matter values (e.g., a single line like
    `primary: #635bff` or `primary: "#635bff"`). The regex matches 3/6/8-digit
    forms with optional surrounding quotes.

    Do NOT apply this to full markdown bodies — it would mutate hex literals
    inside prose code fences (e.g. a Do's section saying "Use `#635bff` for
    primary CTAs"). Restrict to YAML scope; the schema_fixer (Phase 2.5) is
    responsible for splitting front-matter from body before calling.

    Args:
        text: A string (typically a single YAML line or a small YAML block).

    Returns:
        List of hex codes (each prefixed with `#`), in order of appearance.
        Lowercased — caller should normalize input case if matching against
        registry. Returns empty list if no hex found.
    """
    return HEX_PATTERN.findall(text)
