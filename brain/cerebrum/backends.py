"""Cerebrum — Model Backends.

The two non-routing half of the Cerebrum: a local Ollama call and the LiteLLM
fallback used when no Corpus Callosum connector can serve a cloud request.
Extracted from `cerebrum.py` to keep both files under the 200-line limit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from brain.cerebrum.reasoning import ollama_host
from core.config import env, model_for_level, timeout_for_level

DEFAULT_LOCAL_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_CLOUD_MODEL = "groq/openai/gpt-oss-120b"


class BackendError(RuntimeError):
    """Raised when a specific backend cannot produce an answer."""


class CerebrumError(BackendError):
    """Raised when no reasoning backend can produce an answer."""


async def call_local(
    model: str,
    messages: List[Dict[str, str]],
    level: int = 1,
) -> str:
    """Send a non-streaming request to local Ollama.

    Args:
        model: Ollama model name.
        messages: Chat messages including the system prompt.
        level: Routing level, used for the request timeout.

    Returns:
        The generated reply.

    Raises:
        BackendError: If Ollama is unreachable or returns nothing usable.
    """
    payload = {"model": model, "messages": messages, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=timeout_for_level(level)) as client:
            response = await client.post(f"{ollama_host()}/api/chat", json=payload)
            response.raise_for_status()
            content = str(response.json().get("message", {}).get("content", ""))
    except httpx.HTTPError as exc:
        raise BackendError(f"Local model '{model}' unavailable: {exc}") from exc
    except ValueError as exc:
        raise BackendError(f"Local model '{model}' returned invalid JSON: {exc}") from exc
    if not content.strip():
        raise BackendError(f"Local model '{model}' returned an empty response")
    return content


async def call_litellm(
    messages: List[Dict[str, str]],
    level: int = 3,
    model: Optional[str] = None,
) -> str:
    """Send a request through LiteLLM's unified provider list.

    This is the fallback for providers that have no dedicated connector, and the
    path used when a provider needs a feature the connectors do not expose.

    Args:
        messages: Chat messages including the system prompt.
        level: Routing level, selects the cloud model and timeout.
        model: Optional model override.

    Returns:
        The generated reply.

    Raises:
        BackendError: If LiteLLM is missing or the call fails.
    """
    try:
        import litellm  # lazy import: heavy and only needed on the cloud path
    except ImportError as exc:
        raise BackendError("litellm is not installed - run: pip install litellm") from exc

    chosen = model or model_for_level(level) or env("CLOUD_MODEL", DEFAULT_CLOUD_MODEL)
    try:
        response = await litellm.acompletion(
            model=chosen, messages=messages, timeout=timeout_for_level(level)
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:  # LiteLLM raises provider-specific exception types
        raise BackendError(f"Cloud model '{chosen}' failed: {exc}") from exc
    if not content.strip():
        raise BackendError(f"Cloud model '{chosen}' returned an empty response")
    return content
