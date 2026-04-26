"""Tests for D2 dark mode dual-run (Phase 3a 3a.5).

Covers:
- BrowserSession.set_color_scheme call shape (`set media`, NOT `color-scheme`)
- extract_dual_mode session lifecycle (manual, single navigation, idempotent close)
- diff_registries (identical / single change / asymmetric keys)
- build_dark_section markdown output
- preflight() (version + probe gating)
- _maybe_emit_dark_section (omit on empty diff, append on non-empty)
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from design_from_url import dark_mode
from design_from_url.dark_mode import (
    DarkModeUnsupported,
    build_dark_section,
    diff_registries,
    preflight,
)
from design_from_url.renderer import BrowserSession


# ----------------------------------------------------------------------
# 1. BrowserSession.set_color_scheme — CLI shape contract
# ----------------------------------------------------------------------


def test_set_color_scheme_invokes_set_media_dark():
    """Phase 3a 3a.1 verified shape: `agent-browser set media dark`,
    NOT `set color-scheme`."""
    with patch.object(BrowserSession, "_run") as mock_run:
        with patch("shutil.which", return_value="/fake/agent-browser"):
            s = BrowserSession(session_name="test")
            s.set_color_scheme("dark")
            mock_run.assert_called_once_with("set", "media", "dark")


def test_set_color_scheme_invokes_set_media_light():
    with patch.object(BrowserSession, "_run") as mock_run:
        with patch("shutil.which", return_value="/fake/agent-browser"):
            s = BrowserSession(session_name="test")
            s.set_color_scheme("light")
            mock_run.assert_called_once_with("set", "media", "light")


def test_set_color_scheme_rejects_invalid():
    with patch("shutil.which", return_value="/fake/agent-browser"):
        s = BrowserSession(session_name="test")
        with pytest.raises(ValueError, match="must be 'light' or 'dark'"):
            s.set_color_scheme("auto")


# ----------------------------------------------------------------------
# 2. extract_dual_mode — session lifecycle
# ----------------------------------------------------------------------


def _build_fake_session():
    """Minimal session double covering all methods extract_dual_mode invokes."""
    s = MagicMock()
    s.eval_js.side_effect = lambda script: (
        # Two pseudo-payloads + one html_size eval call between them.
        # Order: html_size → light eval → dark eval (per extract_dual_mode body).
        "<html></html>" if "outerHTML" in script else {
            "root_vars": {},
            "computed_styles": [],
            "button_backgrounds": [],
            "length_histogram": {"padding": [], "border-radius": [], "gap": []},
            "length_histogram_meta": {"elements_scanned": 0, "fields": []},
            "viewport": {"width": 1440, "height": 900, "dpr": 1},
        }
    )
    s.get_url.return_value = "https://example.com/landed"
    s.get_title.return_value = "Example"
    return s


def test_extract_dual_mode_manual_lifecycle_calls():
    """extract_dual_mode constructs the session manually:
    open_url once, set_color_scheme twice (light then dark), close once."""
    fake_session = _build_fake_session()

    with patch("design_from_url.extractor.BrowserSession", return_value=fake_session) \
            if False else patch(
                "design_from_url.renderer.BrowserSession",
                return_value=fake_session,
            ):
        with patch("design_from_url.consent.dismiss_consent"):
            from design_from_url.extractor import extract_dual_mode
            light, dark = extract_dual_mode("https://example.com")

    fake_session.set_viewport.assert_called_once_with(1440, 900)
    fake_session.open_url.assert_called_once()
    # 2 color-scheme flips: light first then dark
    assert fake_session.set_color_scheme.call_args_list == [
        ((("light",), {})),
        ((("dark",), {})),
    ]
    fake_session.close.assert_called_once()


def test_extract_dual_mode_single_navigation():
    """One open_url for both passes — that's the architectural promise."""
    fake_session = _build_fake_session()
    with patch(
        "design_from_url.renderer.BrowserSession", return_value=fake_session,
    ):
        with patch("design_from_url.consent.dismiss_consent"):
            from design_from_url.extractor import extract_dual_mode
            extract_dual_mode("https://example.com")
    assert fake_session.open_url.call_count == 1


def test_extract_dual_mode_close_called_on_exception():
    """try/finally: close() runs even when extraction inside raises."""
    fake_session = _build_fake_session()
    fake_session.set_color_scheme.side_effect = RuntimeError("boom")

    with patch(
        "design_from_url.renderer.BrowserSession", return_value=fake_session,
    ):
        with patch("design_from_url.consent.dismiss_consent"):
            from design_from_url.extractor import extract_dual_mode
            with pytest.raises(RuntimeError, match="boom"):
                extract_dual_mode("https://example.com")
    fake_session.close.assert_called_once()


# ----------------------------------------------------------------------
# 3. diff_registries — semantic correctness
# ----------------------------------------------------------------------


def test_diff_registries_identical_returns_empty():
    L = {"colors": {"primary": "#635bff", "neutral": "#fff"}}
    D = {"colors": {"primary": "#635bff", "neutral": "#fff"}}
    assert diff_registries(L, D) == {}


def test_diff_registries_single_color_change():
    L = {"colors": {"primary": "#635bff", "bg": "#fff"}}
    D = {"colors": {"primary": "#635bff", "bg": "#000"}}
    diff = diff_registries(L, D)
    assert diff == {"bg": {"light": "#fff", "dark": "#000"}}


def test_diff_registries_key_missing_in_dark():
    L = {"colors": {"primary": "#635bff", "accent": "#ff0"}}
    D = {"colors": {"primary": "#635bff"}}  # accent absent in dark
    diff = diff_registries(L, D)
    assert "accent" in diff
    assert diff["accent"] == {"light": "#ff0", "dark": "(missing)"}


def test_diff_registries_handles_list_of_tokens():
    """Aggregator may emit list-of-Token; coercer must handle both."""
    light_tok = SimpleNamespace(name="primary", value="#635bff")
    dark_tok = SimpleNamespace(name="primary", value="#9d96ff")
    L = SimpleNamespace(colors=[light_tok])
    D = SimpleNamespace(colors=[dark_tok])
    diff = diff_registries(L, D)
    assert diff == {"primary": {"light": "#635bff", "dark": "#9d96ff"}}


def test_diff_registries_handles_tuple_of_tokens():
    """REGRESSION: real Registry.colors is `tuple` (frozen dataclass), not list.
    Earlier _coerce_color_map only checked `isinstance(colors, list)` — silently
    returned {} for real registries, making every E2E diff empty."""
    light_tok = SimpleNamespace(name="primary", value="#635bff")
    dark_tok = SimpleNamespace(name="primary", value="#9d96ff")
    L = SimpleNamespace(colors=(light_tok,))    # tuple, not list
    D = SimpleNamespace(colors=(dark_tok,))
    diff = diff_registries(L, D)
    assert diff == {"primary": {"light": "#635bff", "dark": "#9d96ff"}}


# ----------------------------------------------------------------------
# 4. build_dark_section — markdown output
# ----------------------------------------------------------------------


def test_build_dark_section_empty_returns_empty_string():
    assert build_dark_section({}) == ""


def test_build_dark_section_renders_markdown_table():
    diff = {
        "primary": {"light": "#635bff", "dark": "#9d96ff"},
        "bg": {"light": "#fff", "dark": "#0a0a0a"},
    }
    out = build_dark_section(diff)
    assert "## Dark Mode" in out
    assert "| Token | Light | Dark |" in out
    assert "|-------|-------|------|" in out
    assert "`primary`" in out
    assert "`#635bff`" in out
    assert "`#9d96ff`" in out


# ----------------------------------------------------------------------
# 5. preflight — version + probe gating
# ----------------------------------------------------------------------


def test_preflight_raises_when_version_too_old():
    with patch("design_from_url.dark_mode.shutil.which", return_value="/fake/ab"):
        fake_proc = MagicMock(returncode=0, stdout="agent-browser 0.25.0\n", stderr="")
        with patch("design_from_url.dark_mode.subprocess.run", return_value=fake_proc):
            with pytest.raises(DarkModeUnsupported, match="older than the required"):
                preflight()


def test_preflight_raises_when_probe_returns_false():
    """Version OK but probe fails → DarkModeUnsupported."""
    with patch("design_from_url.dark_mode.shutil.which", return_value="/fake/ab"):
        fake_proc = MagicMock(returncode=0, stdout="agent-browser 0.26.0\n", stderr="")
        with patch("design_from_url.dark_mode.subprocess.run", return_value=fake_proc):
            # Bypass lru_cache so test can re-stub
            dark_mode._probe_dark_mode_support.cache_clear()
            with patch("design_from_url.dark_mode._probe_dark_mode_support", return_value=False):
                with pytest.raises(DarkModeUnsupported, match="probe failed at runtime"):
                    preflight()


def test_preflight_raises_when_agent_browser_missing():
    with patch("design_from_url.dark_mode.shutil.which", return_value=None):
        with pytest.raises(DarkModeUnsupported, match="not found on PATH"):
            preflight()


# ----------------------------------------------------------------------
# 6. _maybe_emit_dark_section — DESIGN.md append behavior
# ----------------------------------------------------------------------


def test_maybe_emit_dark_section_omits_when_payload_none():
    """No payload → no-op (silent)."""
    from design_from_url.cli import _maybe_emit_dark_section
    args = SimpleNamespace(k_max=5, delta_e=6.0)
    _maybe_emit_dark_section(
        out_path="/nonexistent",
        primary_registry=None,
        dark_payload=None,
        args=args,
        primary=None,
    )
    # No exception, no file write — no assertion needed beyond return.


def test_maybe_emit_dark_section_omits_when_diff_empty(tmp_path, capfd):
    """Empty diff → INFO log + no append."""
    from design_from_url.cli import _maybe_emit_dark_section
    out = tmp_path / "DESIGN.md"
    out.write_text("# original\n", encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    args = SimpleNamespace(k_max=5, delta_e=6.0)
    fake_registry = SimpleNamespace(colors=[])
    with patch("design_from_url.cli.diff_registries" if False else
               "design_from_url.dark_mode.diff_registries", return_value={}):
        with patch("design_from_url.aggregator.aggregate_spacing_and_rounded",
                   return_value={"spacing": [], "rounded": []}):
            with patch("design_from_url.colors.collect_color_strings", return_value=[]):
                with patch("design_from_url.colors.dedupe_colors", return_value=[]):
                    with patch("design_from_url.registry.build_registry",
                               return_value=fake_registry):
                        _maybe_emit_dark_section(
                            out_path=str(out),
                            primary_registry=fake_registry,
                            dark_payload={"colors": []},
                            args=args,
                            primary=None,
                        )

    err = capfd.readouterr().err
    assert "no dark styling" in err.lower()
    # File unchanged
    assert out.read_text(encoding="utf-8") == before


def test_maybe_emit_dark_section_appends_when_diff_nonempty(tmp_path):
    """Non-empty diff → `## Dark Mode` section appended."""
    from design_from_url.cli import _maybe_emit_dark_section
    out = tmp_path / "DESIGN.md"
    out.write_text("# original\n", encoding="utf-8")

    args = SimpleNamespace(k_max=5, delta_e=6.0)
    fake_registry = SimpleNamespace(colors=[])
    fake_diff = {"primary": {"light": "#fff", "dark": "#000"}}
    with patch("design_from_url.dark_mode.diff_registries", return_value=fake_diff):
        with patch("design_from_url.aggregator.aggregate_spacing_and_rounded",
                   return_value={"spacing": [], "rounded": []}):
            with patch("design_from_url.colors.collect_color_strings", return_value=[]):
                with patch("design_from_url.colors.dedupe_colors", return_value=[]):
                    with patch("design_from_url.registry.build_registry",
                               return_value=fake_registry):
                        _maybe_emit_dark_section(
                            out_path=str(out),
                            primary_registry=fake_registry,
                            dark_payload={"colors": []},
                            args=args,
                            primary=None,
                        )

    after = out.read_text(encoding="utf-8")
    assert "# original" in after
    assert "## Dark Mode" in after
    assert "`primary`" in after
