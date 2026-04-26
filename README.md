# design-from-url

Extract design tokens from a URL and emit a `DESIGN.md` file conformant to the
[`@google/design.md`](https://www.npmjs.com/package/@google/design.md) spec
(pinned to `0.1.1`).

> **Disclaimer.** Generated `DESIGN.md` is for personal reference / internal
> style transfer. Trademarks, brand identity, and proprietary visual systems
> remain owned by their original sites — output is not authorized for public
> redistribution.

## Status

Phase 3a complete (v0.3.0a0, 2026-04-26). AC#6 (`--with-llm` 3-site E2E)
confirmed PASS on Stripe, Linear, Vercel — all sites produce
spec-compliant `DESIGN.md` (`design.md lint` errors=0). Phase 0 spike
PASSED on 5 reference sites (Stripe, Linear, Vercel, Tailwind, Notion);
see `.specs/changes/design-md-from-url/spike/decision.md` in the parent
dotclaude repo.

## Quick start

```bash
# Python 3.13 required
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .

# Smoke check
python -m design_from_url --help
```

Runtime browser is provided by [`agent-browser`](https://github.com/instawow/agent-browser)
(must be on `PATH` — install via `brew install agent-browser` or
`npm install -g agent-browser`). agent-browser drives the user's system Chrome
via a persistent daemon, which avoids a separate Chromium download and gives
better anti-bot resistance than headless Playwright.

## Architecture

```
URL [+ optional --primary <hex>]
  -> Renderer (agent-browser + consent dismiss)
  -> Extractor (:root vars + computed styles + button bg)
  -> Aggregator (color dedupe + spacing/rounded clustering)
  -> Token Registry (with optional --primary inject)
  -> Vision LLM (role mapping + prose)
  -> Schema Fixer (broken-ref convergence)
  -> Self-lint (npx @google/design.md@0.1.1)
  -> DESIGN.md
```

See `.specs/changes/design-md-from-url/design.md` in the parent dotclaude repo
for the full architectural spec.

## Phase scope

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Vision-LLM quality spike (5 sites, binary go/no-go gate) | PASS (89%) |
| 1 | Token extraction + Schema Fixer skeleton | PASS |
| 2 | Vision LLM prose + component identification + self-lint loop | PASS |
| 3a | Exit codes + dark mode + `<a>` traversal + prose 4 sections | PASS (3-site E2E confirmed) |
| 3b | LLM provider switching + 10-site sweep + value-based dark diff | pending |
