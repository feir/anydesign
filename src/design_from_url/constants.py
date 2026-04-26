"""Pinned external-tooling versions and shared constants.

The canonical source of truth for `DESIGN_MD_NPM_VERSION` is the
`[tool.design-from-url]` section in `pyproject.toml`; this module mirrors it
for runtime use. A CI check (out of v1 scope) should assert the two stay in
sync.
"""

# Pinned npm version of @google/design.md spec/lint CLI.
# Must match pyproject.toml [tool.design-from-url].design_md_npm_version.
DESIGN_MD_NPM_VERSION = "0.1.1"

# npx package coordinate used by preflight + self-lint.
DESIGN_MD_NPM_PACKAGE = f"@google/design.md@{DESIGN_MD_NPM_VERSION}"

# Generator string written into DESIGN.md metadata.
# Single-sourced from __init__.__version__ (Phase 3a 3a.7) — DO NOT
# hardcode another version string anywhere; the metadata test asserts
# all 3 sources (pyproject.toml, __version__, GENERATOR) match.
from design_from_url import __version__
GENERATOR = f"design-from-url v{__version__}"

# Minimum agent-browser version supporting `set media [dark|light]`.
# Verified by 3a.1 probe; runtime check raises DarkModeUnsupported below
# this threshold (env-dependent capability — committing a fixed True/False
# constant would lie when users have older binaries).
MIN_AGENT_BROWSER_VERSION = "0.26.0"
