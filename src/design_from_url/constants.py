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
GENERATOR = "design-from-url v0.1"
