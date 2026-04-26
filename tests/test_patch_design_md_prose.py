"""Tests for _patch_design_md with the Phase 3a `prose_sections` arg.

Covers:
- 4 prose placeholders replaced when prose_sections dict provided
- Legacy 'deferred' stub used when arg omitted (Phase 2 backward compat)
- Partial dict (3 of 4 keys) — provided keys replaced, missing keys use stub
"""

from __future__ import annotations

import os
import tempfile

from design_from_url.cli import _patch_design_md


_TEMPLATE = """\
---
url: example.com
---
# DESIGN

## Overview
<!-- LLM_PLACEHOLDER:overview -->

## Colors
<!-- LLM_PLACEHOLDER:colors_prose -->

## Typography
<!-- LLM_PLACEHOLDER:typography_prose -->

## Layout
<!-- LLM_PLACEHOLDER:layout_prose -->

## Components
<!-- LLM_PLACEHOLDER:components_prose -->

## Do's & Don'ts
<!-- LLM_PLACEHOLDER:dos_donts -->
"""


def _write_template() -> str:
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE)
    return path


def test_patch_with_prose_sections_replaces_all_4():
    path = _write_template()
    try:
        _patch_design_md(
            path,
            overview_text="Overview body.",
            dos_text="Dos body.",
            component_yaml="",
            prose_sections={
                "colors_prose": "Color paragraph.",
                "typography_prose": "Type paragraph.",
                "layout_prose": "Layout paragraph.",
                "components_prose": "Components paragraph.",
            },
        )
        with open(path, encoding="utf-8") as f:
            text = f.read()
    finally:
        os.unlink(path)

    # All 4 prose placeholders replaced
    assert "<!-- LLM_PLACEHOLDER:colors_prose -->" not in text
    assert "<!-- LLM_PLACEHOLDER:typography_prose -->" not in text
    assert "<!-- LLM_PLACEHOLDER:layout_prose -->" not in text
    assert "<!-- LLM_PLACEHOLDER:components_prose -->" not in text
    assert "Color paragraph." in text
    assert "Type paragraph." in text
    assert "Layout paragraph." in text
    assert "Components paragraph." in text
    # No 'deferred' stub left
    assert "prose generation deferred" not in text


def test_patch_without_prose_sections_uses_legacy_stub():
    """Backward compat: callers that don't pass prose_sections get the
    legacy deferred-stub behavior (Phase 2 path)."""
    path = _write_template()
    try:
        _patch_design_md(
            path,
            overview_text="Overview.",
            dos_text="Dos.",
            component_yaml="",
        )
        with open(path, encoding="utf-8") as f:
            text = f.read()
    finally:
        os.unlink(path)

    assert text.count("_(prose generation deferred to Phase 2.x)_") == 4


def test_patch_with_partial_prose_sections():
    """Missing keys fall back to legacy stub."""
    path = _write_template()
    try:
        _patch_design_md(
            path,
            overview_text="O.",
            dos_text="D.",
            component_yaml="",
            prose_sections={
                "colors_prose": "Colors.",
                "typography_prose": "Type.",
                # layout_prose + components_prose intentionally missing
            },
        )
        with open(path, encoding="utf-8") as f:
            text = f.read()
    finally:
        os.unlink(path)

    assert "Colors." in text
    assert "Type." in text
    assert text.count("_(prose generation deferred to Phase 2.x)_") == 2


def test_patch_with_none_prose_sections_explicit():
    """Explicit None matches default."""
    path = _write_template()
    try:
        _patch_design_md(
            path,
            overview_text="O.",
            dos_text="D.",
            component_yaml="",
            prose_sections=None,
        )
        with open(path, encoding="utf-8") as f:
            text = f.read()
    finally:
        os.unlink(path)

    assert text.count("_(prose generation deferred to Phase 2.x)_") == 4
