"""Tests for Phase 3a 3a.2 — CLI exit code contract wiring.

Covers:
- url_parse_failed: malformed URLs → exit 1
- render_timeout: agent-browser timeout → exit 1
- registry_empty: 0 colors extracted → exit 1
- lint_cli_missing: preflight failure → exit 1
- _validate_url helper: valid + invalid cases
- _emit_degraded_warning: stderr format
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from design_from_url.cli import _validate_url, _emit_degraded_warning, _cmd_build, _cmd_preflight
from design_from_url.run_report import STATUS_MAP


# ---- _validate_url ----

def test_validate_url_accepts_https():
    assert _validate_url("https://stripe.com") == "https://stripe.com"


def test_validate_url_accepts_http():
    assert _validate_url("http://example.com") == "http://example.com"


def test_validate_url_accepts_file():
    assert _validate_url("file:///tmp/probe.html") == "file:///tmp/probe.html"


def test_validate_url_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        _validate_url("")


def test_validate_url_rejects_no_scheme():
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("stripe.com")


def test_validate_url_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("ftp://example.com")


def test_validate_url_rejects_https_no_host():
    with pytest.raises(ValueError, match="host"):
        _validate_url("https://")


# ---- _emit_degraded_warning ----

def test_emit_degraded_warning_writes_to_stderr(capsys):
    _emit_degraded_warning("url_parse_failed", 1)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "WARNING:" in captured.err
    assert "url_parse_failed" in captured.err
    assert "exit_code=1" in captured.err


# ---- _cmd_build URL parse wiring ----

def _build_args(**overrides):
    """Helper: build a minimal args.Namespace for _cmd_build."""
    import argparse
    defaults = dict(
        url="https://stripe.com",
        out=None,
        primary=None,
        no_auto_primary=True,  # avoid screenshot path
        no_consent_dismiss=False,
        timeout=30,
        k_max=8,
        delta_e=10.0,
        cap_colors=0,
        with_llm=False,
        llm_model="local/local-main",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_cmd_build_url_parse_failed_returns_exit_1(capsys):
    args = _build_args(url="not-a-url")
    rc = _cmd_build(args)
    assert rc == STATUS_MAP["url_parse_failed"][1]  # 1
    captured = capsys.readouterr()
    assert "FATAL: invalid URL" in captured.err
    assert "url_parse_failed" in captured.err


def test_cmd_build_empty_url_returns_exit_1():
    args = _build_args(url="")
    rc = _cmd_build(args)
    assert rc == 1


def test_cmd_build_unknown_scheme_returns_exit_1():
    args = _build_args(url="ftp://example.com/")
    rc = _cmd_build(args)
    assert rc == 1


# ---- render_timeout wiring ----

def test_cmd_build_render_timeout_returns_exit_1(capsys):
    """If extract_from_url raises RenderError with 'timed out' message → exit 1."""
    from design_from_url.renderer import RenderError
    args = _build_args(url="https://valid.example.com")

    with patch("design_from_url.extractor.extract_from_url",
               side_effect=RenderError("agent-browser timed out after 30s: open ...")):
        rc = _cmd_build(args)
    assert rc == STATUS_MAP["render_timeout"][1]  # 1
    captured = capsys.readouterr()
    assert "render timeout" in captured.err.lower()
    assert "render_timeout" in captured.err


def test_cmd_build_non_timeout_render_error_propagates():
    """RenderError WITHOUT 'timed out' should propagate (not mapped to render_timeout)."""
    from design_from_url.renderer import RenderError
    args = _build_args(url="https://valid.example.com")

    with patch("design_from_url.extractor.extract_from_url",
               side_effect=RenderError("agent-browser open exited 1: bad URL")):
        with pytest.raises(RenderError):
            _cmd_build(args)


# ---- registry_empty wiring ----

def test_cmd_build_registry_empty_returns_exit_1(capsys):
    """If registry.colors is empty after build → exit 1."""
    args = _build_args(url="https://valid.example.com")

    fake_registry = MagicMock()
    fake_registry.colors = {}  # empty

    with patch("design_from_url.extractor.extract_from_url",
               return_value={"url": "https://valid.example.com", "html_size": 1000,
                             "computed_styles": [], "button_backgrounds": [],
                             "length_histogram": []}), \
         patch("design_from_url.aggregator.aggregate_spacing_and_rounded",
               return_value={"spacing": [], "rounded": []}), \
         patch("design_from_url.colors.collect_color_strings", return_value=[]), \
         patch("design_from_url.colors.dedupe_colors", return_value=[]), \
         patch("design_from_url.registry.build_registry", return_value=fake_registry):
        rc = _cmd_build(args)
    assert rc == STATUS_MAP["registry_empty"][1]  # 1
    captured = capsys.readouterr()
    assert "0 colors" in captured.err
    assert "registry_empty" in captured.err


# ---- lint_cli_missing wiring (preflight) ----

def test_cmd_preflight_lint_cli_missing_returns_exit_1(capsys):
    """If preflight fails (npm not found, lint CLI unreachable) → exit 1."""
    import argparse
    args = argparse.Namespace()

    fake_result = MagicMock()
    fake_result.ok = False
    fake_result.reason = "npm not found on PATH"

    with patch("design_from_url.preflight.check_npx_design_md", return_value=fake_result):
        rc = _cmd_preflight(args)
    assert rc == STATUS_MAP["lint_cli_missing"][1]  # 1
    captured = capsys.readouterr()
    assert "PREFLIGHT FAIL" in captured.err
    assert "lint_cli_missing" in captured.err


def test_cmd_preflight_ok_returns_exit_0(capsys):
    import argparse
    args = argparse.Namespace()

    fake_result = MagicMock()
    fake_result.ok = True

    with patch("design_from_url.preflight.check_npx_design_md", return_value=fake_result):
        rc = _cmd_preflight(args)
    assert rc == 0
