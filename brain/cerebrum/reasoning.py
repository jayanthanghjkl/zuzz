"""Cerebrum — Reasoning.

Streaming generators and lightweight reasoning helpers used by the Cerebrum.
Kept separate so the main class stays readable and testable without network
calls.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List

import httpx

from core.config import env

LOCAL_MODELS_URL = "/api/tags"
CHAT_URL = "/api/chat"


def ollama_host() -> str:
    """Return the base URL of the local Ollama server."""
    return env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


async def list_local_models(client: httpx.AsyncClient | None = None) -> List[str]:
    """List model names available in local Ollama.

    Args:
        client: Optional pre-built client to reuse.

    Returns:
        Model names, or an empty list when Ollama is unreachable.

    Raises:
        RuntimeError: Never — connection problems surface as an empty list.
    """
    owned = client is None
    client = client or httpx.AsyncClient(timeout=5.0)
    try:
        response = await client.get(f"{ollama_host()}{LOCAL_MODELS_URL}")
        response.raise_for_status()
        payload = response.json()
        return [str(item.get("name", "")) for item in payload.get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owned:
            await client.aclose()


async def stream_local(
    model: str,
    messages: List[Dict[str, str]],
    timeout: int = 60,
) -> AsyncIterator[str]:
    """Stream a local Ollama response token by token.

    Args:
        model: Ollama model name, e.g. ``qwen2.5-coder:1.5b``.
        messages: Chat messages including the system prompt.
        timeout: Request timeout in seconds.

    Yields:
        Text fragments as Ollama emits them.

    Raises:
        ConnectionError: If Ollama cannot be reached.
        RuntimeError: If Ollama returns an error payload.
    """
    payload = {"model": model, "messages": messages, "stream": True}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{ollama_host()}{CHAT_URL}", json=payload
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"Ollama error {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    fragment = _parse_ollama_line(line)
                    if fragment:
                        yield fragment
    except httpx.HTTPError as exc:
        raise ConnectionError(f"Cannot reach Ollama at {ollama_host()}: {exc}") from exc


def _parse_ollama_line(line: str) -> str:
    """Extract the message content from one Ollama stream line.

    Args:
        line: Raw JSON line from the Ollama stream.

    Returns:
        The content fragment, or an empty string for keepalive/error lines.
    """
    import json

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return str(payload.get("message", {}).get("content", ""))


def estimate_confidence(text: str, question_length: int) -> float:
    """Estimate how trustworthy a draft answer looks.

    A cheap heuristic used by the anti-hallucination rule: very short answers to
    long questions, and answers full of hedging words, score lower.

    Args:
        text: The generated answer.
        question_length: Length of the user question in characters.

    Returns:
        Confidence between 0.0 and 1.0.
    """
    if not text.strip():
        return 0.0
    score = 1.0
    hedges = ("i think", "maybe", "not sure", "i don't know", "possibly", "probably not")
    lowered = text.lower()
    hits = sum(1 for hedge in hedges if hedge in lowered)
    score -= 0.15 * hits
    if question_length > 120 and len(text.strip()) < 40:
        score -= 0.35
    if len(text.strip()) < 15 and question_length < 30:
        score -= 0.1
    return max(0.0, min(1.0, round(score, 2)))
