"""Tests for llm.py — Phase 2 LLM wrapper (task 2.2).

CRITICAL regression guard (plan-review C1): vision-bearing calls must NOT
fall back to cloud sonnet (which has no image input). When local oMLX is
down, they must raise LLMUnavailable instead of silently producing
hallucinated prose.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from design_from_url.llm import LLMUnavailable, generate


# ---- Vision call (image_path set) — bypasses cloud fallback ----

def test_vision_call_uses_omlx_chat_directly():
    """When image_path is set, generate() must call _omlx_chat, not chat()."""
    with patch("design_from_url.llm._omlx_chat", return_value="vision response") as omlx, \
         patch("design_from_url.llm._chat") as chat_:
        result = generate(
            "describe this", image_path="/tmp/img.png",
            model="local/gemma4:26b",
        )
    assert result == "vision response"
    omlx.assert_called_once()
    # Critical: chat() (which has fallback) must NOT be called for vision
    chat_.assert_not_called()
    # Verify image was passed to oMLX
    assert omlx.call_args.kwargs["images"] == ["/tmp/img.png"]


def test_vision_call_raises_LLMUnavailable_on_connection_error():
    """C1 regression guard: oMLX down + image_path set → LLMUnavailable, NEVER fallback."""
    with patch(
        "design_from_url.llm._omlx_chat",
        side_effect=ConnectionError("oMLX not running"),
    ), patch("design_from_url.llm._chat") as chat_:
        with pytest.raises(LLMUnavailable, match="cloud fallback would drop image"):
            generate("describe", image_path="/tmp/x.png", model="local/gemma4:26b")
    # Critical: even after _omlx_chat raised, we must NOT have called chat()
    # (which would have silently fallen back to cloud sonsonet without the image)
    chat_.assert_not_called()


def test_vision_call_raises_LLMUnavailable_on_timeout():
    """Timeouts are also unrecoverable for vision calls."""
    with patch(
        "design_from_url.llm._omlx_chat",
        side_effect=TimeoutError("read timed out"),
    ):
        with pytest.raises(LLMUnavailable):
            generate("p", image_path="/tmp/x.png", model="local/gemma4:26b")


def test_vision_call_with_non_local_model_raises_ValueError():
    """vision call requires a local model; cloud models silently drop images."""
    with pytest.raises(ValueError, match="vision calls require a local model"):
        generate("p", image_path="/tmp/x.png", model="haiku")


def test_vision_call_passes_through_unknown_local_alias():
    """Local alias not in _LOCAL_TO_OMLX_MODEL → use alias as-is (forward-compat)."""
    with patch("design_from_url.llm._omlx_chat", return_value="ok") as omlx:
        generate("p", image_path="/tmp/x.png", model="local/future-model:8b")
    # Unknown alias should pass through to oMLX as-is
    assert omlx.call_args.kwargs["model"] == "future-model:8b"


# ---- Text-only call (image_path is None) — fallback OK ----

def test_text_only_call_uses_chat():
    """Without image_path, generate() should use chat() which can fall back to cloud."""
    with patch("design_from_url.llm._chat", return_value="text response") as chat_, \
         patch("design_from_url.llm._omlx_chat") as omlx:
        result = generate("explain this", model="local/gemma4:26b")
    assert result == "text response"
    chat_.assert_called_once()
    # Text path must not bypass to omlx_chat
    omlx.assert_not_called()


def test_text_only_call_with_cloud_model_works():
    """Pure cloud calls (haiku/sonnet) with no image_path go through chat()."""
    with patch("design_from_url.llm._chat", return_value="haiku response"):
        result = generate("p", model="haiku")
    assert result == "haiku response"


# ---- Parameter forwarding ----

def test_max_tokens_and_temperature_forwarded_to_omlx():
    with patch("design_from_url.llm._omlx_chat", return_value="ok") as omlx:
        generate("p", image_path="/tmp/x.png", model="local/gemma4:26b",
                 max_tokens=512, temperature=0.7)
    kwargs = omlx.call_args.kwargs
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.7


def test_max_tokens_and_temperature_forwarded_to_chat():
    with patch("design_from_url.llm._chat", return_value="ok") as chat_:
        generate("p", model="haiku", max_tokens=512, temperature=0.7)
    kwargs = chat_.call_args.kwargs
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.7


# ---- LLMUnavailable inheritance ----

def test_LLMUnavailable_is_an_exception():
    assert issubclass(LLMUnavailable, Exception)
