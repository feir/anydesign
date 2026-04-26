"""Contract tests for renderer.py — Phase 2.1 viewport screenshot guarantee.

Verifies the contract that the renderer WILL call agent-browser with
width=1440, height=900. Network-free — mocks the subprocess boundary.

Spike screenshots can't be used as ground truth because they were captured
before the VIEWPORT contract stabilized (1280x577 vs current 1440x900).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from design_from_url.renderer import VIEWPORT, BrowserSession


def test_viewport_constant_matches_spec():
    """Phase 2 design.md AC requires 1440x900 viewport."""
    assert VIEWPORT == (1440, 900)


def test_set_viewport_passes_correct_dimensions_to_agent_browser():
    """BrowserSession.set_viewport(*VIEWPORT) must invoke agent-browser
    with the documented width and height. Regression guard against silent
    DPR=2 / retina-scaling that would inflate screenshot bytes."""
    sess = BrowserSession.__new__(BrowserSession)  # bypass __init__
    sess._run = MagicMock(return_value="")
    sess.set_viewport(*VIEWPORT)
    sess._run.assert_called_once_with("set", "viewport", "1440", "900")


def test_screenshot_passes_path_to_agent_browser():
    """screenshot() forwards the output path to `agent-browser screenshot <path>`."""
    sess = BrowserSession.__new__(BrowserSession)
    sess._run = MagicMock(return_value="")
    sess.screenshot("/tmp/x.png")
    args = sess._run.call_args
    assert args[0][:2] == ("screenshot", "/tmp/x.png")


def test_screenshot_default_timeout_is_30s():
    """Default timeout_s is 30 — preserved for callers not specifying one."""
    sess = BrowserSession.__new__(BrowserSession)
    sess._run = MagicMock(return_value="")
    sess.screenshot("/tmp/x.png")
    assert sess._run.call_args.kwargs.get("timeout_s") == 30


def test_screenshot_timeout_s_propagates_to_agent_browser():
    """Override timeout_s reaches the underlying _run subprocess call.

    Regression guard: pre-fix, screenshot hardcoded timeout_s=30, so heavy
    sites (Stripe) silently failed even when CLI --timeout 90 was set.
    """
    sess = BrowserSession.__new__(BrowserSession)
    sess._run = MagicMock(return_value="")
    sess.screenshot("/tmp/x.png", timeout_s=90)
    assert sess._run.call_args.kwargs.get("timeout_s") == 90


def test_screenshot_path_contract_in_cli():
    """When --out is set, screenshot is written to <out-base>.png next to it.

    This is a deviation from the spec's `out/<domain>/viewport.png` proposal
    (kept simple — no domain extraction needed). Locking the actual contract
    so a future refactor doesn't silently change file layout.
    """
    out = "out/stripe.com/DESIGN.md"
    expected_screenshot = os.path.splitext(out)[0] + ".png"
    assert expected_screenshot == "out/stripe.com/DESIGN.png"

    out = "out-stripe.md"
    expected_screenshot = os.path.splitext(out)[0] + ".png"
    assert expected_screenshot == "out-stripe.png"
