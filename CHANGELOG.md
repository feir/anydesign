# CHANGELOG

## v0.3.0a0 (2026-04-26)

### Breaking

- HARD_FAIL exit code changed from `2` to `1` (DEGRADED stays at `2`).
  Affected `degraded_reason` values: `omx_failover`, `required_field_unresolvable`.
  Shell scripts that branch on `design-from-url` exit code must update
  their HARD_FAIL test from `[ $? -eq 2 ]` to `[ $? -eq 1 ]`.

### Added

- `--with-dark` flag — extracts both light and dark color schemes from a
  single navigation, then appends a `## Dark Mode` markdown section to
  DESIGN.md with token-level diffs. Requires agent-browser >= 0.26.0;
  preflight + runtime probe gate execution and raise `DarkModeUnsupported`
  with an upgrade hint when capability is missing.
  - **Known limitation**: the diff compares by positional token name
    (`color_1`..`color_N`), which is reassigned per extraction by
    frequency rank. Dark-aware sites (e.g. tailwindcss.com) produce
    real signal at name `color_1` (e.g. `#010712` → `#ffffff` for body
    background), but indices further down may show "shifts" where the
    same hex slides across slot numbers. Phase 3b will align tokens
    by representative value rather than positional index.
  - Sites that don't respond to `prefers-color-scheme` (e.g. Stripe,
    Linear's marketing surfaces) produce an empty diff and the section
    is silently omitted with an stderr INFO log.
- `<a>` button-styled traversal in the JS extractor — closes the
  Phase 2 AC #4 gap where Stripe/Linear hero CTAs (rendered as
  `<a class="cta">`) produced an empty `components.button-primary`.
  4-clause filter: area >= 3000, NOT under `<nav>`, padding max >= 8,
  role="button" OR chroma >= threshold. `<header>` intentionally NOT
  excluded — hero CTAs live there.
- 4 new prose sections — `colors_prose`, `typography_prose`,
  `layout_prose`, `components_prose`. Replaces the Phase 2
  "_(prose generation deferred)_" stubs with LLM-generated paragraphs.
  Per-section retry budget = 1; deterministic fallback on persistent
  failure. Fallback semantics: `0–1` PASS / `>=2` DEGRADED via
  `degraded_reason='prose_partial'`.
- 5 new `degraded_reason` values: `url_parse_failed`, `render_timeout`,
  `lint_cli_missing`, `registry_empty`, `prose_partial`.
- `spike/` artifacts in repo: `scorecard.json` (with `phase_criteria_version`
  forward-compat handle), per-site `tokens-*.yaml` snapshots, and
  Phase 1+2 architectural decision recap in `decision.md`.

### Changed

- `__version__` is now the single canonical source for all version strings.
  `constants.GENERATOR` derives from it via f-string; `pyproject.toml`
  uses hatchling-native `dynamic = ["version"]` reading from
  `src/design_from_url/__init__.py`.
- `BrowserSession.set_color_scheme(scheme)` added (renderer.py) — wraps
  `agent-browser set media <scheme>`. Verified CLI shape; NOT
  `set color-scheme` (which doesn't exist on agent-browser CLI).
- `_extract_with_session(session, info, *, screenshot_path=None)` helper
  extracted from `extract_from_url`. Dark-mode dual-run reuses it across
  both color schemes within a single navigation.

### Fixed

- Padding extraction now reads 4 individual longhand sides
  (`padding-top`, `padding-right`, `padding-bottom`, `padding-left`)
  instead of the shorthand `padding` — CSSOM spec returns "" for
  shorthand when set via individual properties.
- `BrowserSession.screenshot()` now accepts `timeout_s` keyword and
  plumbs it through `_extract_with_session` / `extract_from_url` /
  `extract_dual_mode` — previously hardcoded at 30s, silently ignoring
  CLI `--timeout`. Heavy sites (Stripe) now honor the user-supplied
  timeout for the screenshot RPC step.

### Verified

- AC #6 real `--with-llm` 3-site E2E PASS confirmed on 2026-04-26:
  Stripe, Linear, Vercel — all `final_status=PASS`, `exit_code=0`,
  `schema_findings=0`, `prose_findings=0`, `retry_rounds=0`. Spec lint
  via `design.md lint`: errors=0 on all three (warnings=11 each are
  expected auto-extraction artifact: extracted colors not referenced
  by the single LLM-identified component).

### Dependencies

- Added `packaging >= 23.0` for agent-browser version comparison
  in dark-mode preflight (proper `Version` semantics, not
  lexicographic string compare).
