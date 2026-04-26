"""Tests for D6 version coordination + metadata block (Phase 3a 3a.7).

The 3 version sources MUST stay in lockstep:
- `__init__.__version__`        — the canonical authority
- `pyproject.toml` dynamic      — hatchling reads from __init__.py
- `constants.GENERATOR` string  — f-string derived from __version__

A divergence here ships DESIGN.md files claiming a version that doesn't
match the installed package, breaking downstream consumers that grep
metadata for tooling provenance.
"""

from __future__ import annotations

import re
from importlib.metadata import version as md_version

from design_from_url import __version__
from design_from_url.constants import GENERATOR
from design_from_url.template import build_metadata_comment


def test_5_metadata_fields_present():
    """build_metadata_comment must emit all 5 contract fields."""
    out = build_metadata_comment(
        source_url="https://example.com/",
        extracted_at="2026-04-26T00:00:00Z",
        primary_override="#635bff",
    )
    assert re.search(r"source_url:\s*\S+", out)
    assert re.search(r"extracted_at:\s*\S+", out)
    assert re.search(r"spec_version:\s*\S+", out)
    assert re.search(r"generator:\s*\S+", out)
    assert "notes:" in out


def test_generator_contains_version():
    """GENERATOR must reference __version__, not a hardcoded literal."""
    assert __version__ in GENERATOR
    assert GENERATOR == f"design-from-url v{__version__}"


def test_3_version_sources_equal():
    """pyproject.toml + __version__ + GENERATOR must reference same version.

    pyproject.toml uses hatchling dynamic resolving against __init__.py;
    importlib.metadata.version() is the post-install canonical source.
    """
    pkg_v = md_version("design-from-url")
    assert pkg_v == __version__, (
        f"pyproject metadata version {pkg_v!r} drifted from "
        f"__init__.__version__ {__version__!r}"
    )
    assert pkg_v in GENERATOR


def test_metadata_in_real_design_md_output():
    """Embed metadata in a synthetic DESIGN.md and grep all 5 fields.

    Defends against future regressions where a refactor moves the comment
    block format and breaks downstream parsers that grep metadata."""
    out = build_metadata_comment(
        source_url="https://stripe.com/",
        extracted_at="2026-04-26T03:50:04+00:00",
        primary_override="#635bff",
    )
    sample = f"---\nname: Test\n---\n\n{out}\n\n# Test body\n"
    for field in ("source_url", "extracted_at", "spec_version", "generator", "notes"):
        assert re.search(rf"{field}\s*:", sample), (
            f"metadata field {field!r} missing from rendered output"
        )
