"""Thin wrapper around shared/lib/llm_client.py with vision-call protection.

CRITICAL contract (plan-review C1): `llm_client.chat()` auto-falls back from
local oMLX to cloud sonnet for `gemma4:26b` and silently drops `images=`
during fallback (llm_client.py:382-396). For vision-bearing calls we MUST
bypass that fallback, since the cloud model has no image input — falling
back would silently produce hallucinated prose.

Strategy:
- `image_path is None` (text-only)  → use `chat()` with cloud fallback OK
- `image_path is set` (vision call) → call `_omlx_chat` directly, raise
  `LLMUnavailable` on `ConnectionError` / `TimeoutError`

Reaching into `_omlx_chat` (private) is intentional: `chat()` does not
expose a `disable_fallback` parameter. The alternative (logging-hook to
detect "Falling back to Claude" warnings) is brittle and race-prone.
If the shared lib refactors `_omlx_chat` away, this wrapper breaks loudly
at import time, not silently at runtime.
"""

from __future__ import annotations

import os
import sys

# Add shared lib path so the import below works without an editable install.
_SHARED_LIB = os.path.expanduser("~/projects/shared/lib")
if _SHARED_LIB not in sys.path:
    sys.path.insert(0, _SHARED_LIB)

from llm_client import (  # noqa: E402  (path-mutation must precede import)
    DEFAULT_LOCAL_MODEL,
    _LOCAL_TO_OMLX_MODEL,
    _omlx_chat,
    chat as _chat,
)


class LLMUnavailable(Exception):
    """Raised when a vision call cannot be served because local oMLX is down.

    Vision calls cannot fall back to cloud — the shared lib's cloud backend
    has no image input. Caller (CLI) should exit 2 + write
    `run_report.json` with `degraded_reason="omx_failover"`.
    """


def generate(
    prompt: str,
    *,
    image_path: str | None = None,
    model: str = DEFAULT_LOCAL_MODEL,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    timeout: int = 120,
) -> str:
    """Call an LLM and return text response.

    Routing:
        - image_path is None → uses `llm_client.chat()` (cloud fallback OK)
        - image_path is set  → calls `_omlx_chat` directly (no fallback)

    Args:
        prompt: User prompt text.
        image_path: Optional path to a local image file (e.g. viewport.png).
            Must be a path that `_omlx_chat` can read; URLs not accepted by
            this wrapper (the shared lib handles URL fetch but we keep the
            contract tight here).
        model: Model name. For vision calls must start with "local/".
        max_tokens: Max output tokens (oMLX only).
        temperature: Sampling temperature (oMLX only).
        timeout: Request timeout in seconds.

    Returns:
        The model's response text (stripped).

    Raises:
        LLMUnavailable: When vision call fails because oMLX is unreachable.
        ValueError: When `image_path` is set but `model` is not a local model.
        RuntimeError: For non-recoverable errors from either backend.
    """
    if image_path is None:
        # Text-only path: fallback acceptable
        return _chat(
            prompt, model=model,
            max_tokens=max_tokens, temperature=temperature, timeout=timeout,
        )

    # Vision path: bypass chat()'s cloud fallback (it drops images silently).
    if not model.startswith("local/"):
        raise ValueError(
            f"vision calls require a local model (image_path is set); got {model!r}. "
            f"Pass model='local/gemma4:26b' or similar."
        )
    local_alias = model.removeprefix("local/")
    omlx_model = _LOCAL_TO_OMLX_MODEL.get(local_alias, local_alias)
    try:
        return _omlx_chat(
            prompt,
            model=omlx_model,
            images=[image_path],
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except (ConnectionError, TimeoutError) as exc:
        raise LLMUnavailable(
            f"local oMLX unavailable for vision call (cloud fallback would drop image): {exc}"
        ) from exc
