"""Tests for the Groq connector: auth, streaming, rate limits and failures."""

from __future__ import annotations

import json

import httpx
import pytest

from brain.corpus_callosum.connector_state import ConnectorError, RateLimitedError
from brain.corpus_callosum.groq_connector import GroqConnector
from tests.provider_mocks import MESSAGES, SSE_BODY, mock_http, openai_reply


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------


async def test_groq_chat_returns_text(monkeypatch) -> None:
    """A Groq chat response is parsed into plain text."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return openai_reply("hi from groq")(request)

    connector = GroqConnector()
    with mock_http(monkeypatch, handler):
        assert await connector.chat(MESSAGES) == "hi from groq"

    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk-test"
    assert seen["body"]["model"] == GroqConnector.DEFAULT_MODEL
    assert (await connector.status()).state == "healthy"


async def test_groq_streams_tokens(monkeypatch) -> None:
    """Streaming yields the deltas and then completes."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SSE_BODY, headers={"content-type": "text/event-stream"})

    fragments = []
    with mock_http(monkeypatch, handler):
        async for fragment in GroqConnector().stream_chat(MESSAGES):
            fragments.append(fragment)
    assert "".join(fragments) == "hello"


async def test_groq_rate_limit_error_becomes_rate_limited(monkeypatch) -> None:
    """HTTP 429 surfaces as RateLimitedError and marks the provider."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    connector = GroqConnector()
    with mock_http(monkeypatch, handler):
        with pytest.raises(RateLimitedError):
            await connector.chat(MESSAGES)
    assert (await connector.status()).state == "rate_limited"


async def test_groq_server_error_marks_offline(monkeypatch) -> None:
    """Repeated server failures move the provider to offline."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    connector = GroqConnector()
    for _ in range(2):
        with mock_http(monkeypatch, handler):
            with pytest.raises(ConnectorError):
                await connector.chat(MESSAGES)
    status = await connector.status()
    assert status.state == "offline"
    assert status.failure_count == 2


async def test_groq_empty_response_is_an_error(monkeypatch) -> None:
    """A blank answer is a provider failure, not a valid reply."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    with mock_http(monkeypatch, handler):
        with pytest.raises(ConnectorError):
            await GroqConnector().chat(MESSAGES)


def test_groq_stream_chunk_parsing() -> None:
    """SSE lines are parsed and terminators ignored."""
    connector = GroqConnector()
    assert connector.parse_chunk('data: {"choices":[{"delta":{"content":"a"}}]}') == "a"
    assert connector.parse_chunk("data: [DONE]") == ""
    assert connector.parse_chunk("") == ""
    assert connector.parse_chunk("data: not-json") == ""


async def test_groq_rate_limit_status(monkeypatch) -> None:
    """Quota usage is reported for the dashboard."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return openai_reply("ok")(request)

    connector = GroqConnector()
    with mock_http(monkeypatch, handler):
        await connector.chat(MESSAGES)
    status = await connector.get_rate_limit_status()
    assert status["provider"] == "groq"
    assert status["limit"] == 30
    assert status["used"] == 1


async def test_groq_health_check(monkeypatch) -> None:
    """A reachable provider passes the health probe."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/models")
        return httpx.Response(200, json={"data": []})

    with mock_http(monkeypatch, handler):
        assert await GroqConnector().health_check() is True


