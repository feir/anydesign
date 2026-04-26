"""Per-section prose generation with retry + deterministic fallback.

D4 contract — Phase 3a:

- Each of (colors_prose, typography_prose, layout_prose, components_prose)
  runs `llm.generate` with a 1-retry budget.
- A section "fails" when both attempts raise an exception or produce
  output that is empty / too short / echoes the legacy "deferred"
  placeholder language.
- Failed sections fall back to a deterministic stub. The stub MUST NOT
  contain the word "deferred" — that's the legacy Phase 2 placeholder
  string and using it would cause the self-lint loop to re-flag the
  section every round.
- Caller sums fallbacks. Single-threshold rule:
    * `fallback_count == 0`  → PASS
    * `fallback_count == 1`  → PASS (log INFO; minor cosmetic gap)
    * `fallback_count >= 2`  → DEGRADED, `degraded_reason='prose_partial'`,
                               exit 2
"""

from __future__ import annotations

from typing import Callable, Iterable

from design_from_url import llm as _llm
from design_from_url.prompt_loader import load_prompt


PROSE_SECTION_KEYS: tuple[str, ...] = (
    "colors_prose",
    "typography_prose",
    "layout_prose",
    "components_prose",
)

# Minimum substantive output length. 30 chars is loose enough to allow
# concise paragraphs but rejects empty / one-word echoes.
_MIN_VALID_LEN = 30


_FALLBACK_TEMPLATES: dict[str, str] = {
    "colors_prose": (
        "Auto-generated summary: the captured color tokens "
        "(see YAML frontmatter) define the working palette. "
        "Refer to token names for role mapping — primary for brand "
        "expression, neutral for surfaces, accent for emphasis."
    ),
    "typography_prose": (
        "Auto-generated summary: typography tokens are listed in the "
        "YAML frontmatter. Pair display / heading tokens with body tokens "
        "to express hierarchy; weight contrast typically carries emphasis."
    ),
    "layout_prose": (
        "Auto-generated summary: spacing and rounded tokens "
        "(see YAML frontmatter) form the layout primitives. "
        "Larger spacing units organize sections; rounded values shape "
        "interactive surfaces."
    ),
    "components_prose": (
        "Auto-generated summary: component definitions reference registry "
        "color tokens; see frontmatter for `button-primary` binding. "
        "Secondary actions and affordances follow the same registry "
        "conventions."
    ),
}


def _is_valid(text: str | None) -> bool:
    """A section's output is valid when non-empty, of reasonable length,
    and not echoing the legacy Phase 2 'deferred' stub language."""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_VALID_LEN:
        return False
    if "deferred" in stripped.lower():
        return False
    return True


def _build_fallback(key: str) -> str:
    """Deterministic fallback when both LLM attempts fail.

    Returns a registry-agnostic substantive paragraph. MUST NOT contain
    the word 'deferred' (would echo Phase 2 placeholder semantics).
    """
    return _FALLBACK_TEMPLATES.get(
        key, f"Auto-generated summary for {key}.",
    )


def generate_prose_section(
    key: str,
    *,
    registry_yaml: str,
    screenshot_path: str | None,
    model: str,
    llm_generate: Callable[..., str] = _llm.generate,
) -> tuple[str, bool]:
    """Generate prose for one section with 1-retry budget.

    Returns (text, used_fallback). `used_fallback=True` means both LLM
    attempts failed and the deterministic stub was used.
    """
    prompt = load_prompt(key, registry=registry_yaml)
    last_attempt: str | None = None
    for _attempt in (1, 2):
        try:
            out = llm_generate(prompt, image_path=screenshot_path, model=model)
        except _llm.LLMUnavailable:
            continue
        except Exception:
            # Treat any other exception during generation as attempt failure.
            # Don't crash the pipeline over a single bad section.
            continue
        last_attempt = out
        if _is_valid(out):
            return out.strip(), False
    # Both attempts exhausted (or all returned invalid output).
    return _build_fallback(key), True


def generate_all_prose_sections(
    *,
    registry_yaml: str,
    screenshot_path: str | None,
    model: str,
    keys: Iterable[str] = PROSE_SECTION_KEYS,
    llm_generate: Callable[..., str] = _llm.generate,
) -> tuple[dict[str, str], int]:
    """Generate all 4 prose sections.

    Returns (section_texts, fallback_count) where:
    - section_texts maps each key to its final markdown paragraph
    - fallback_count is the number of sections that exhausted retry
    """
    texts: dict[str, str] = {}
    fallback_count = 0
    for key in keys:
        text, fell_back = generate_prose_section(
            key,
            registry_yaml=registry_yaml,
            screenshot_path=screenshot_path,
            model=model,
            llm_generate=llm_generate,
        )
        texts[key] = text
        if fell_back:
            fallback_count += 1
    return texts, fallback_count
