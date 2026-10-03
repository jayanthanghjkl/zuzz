"""Tests for the Colab connector.

The notebook is treated as keyless: its ngrok URL is the credential, and that
URL changes every time the Colab session restarts.
"""

from __future__ import annotations

import json

import httpx
import pytest

from brain.corpus_callosum.colab_connector import ColabConnector
from tests.provider_mocks import MESSAGES, mock_http


# --------------------------------------------------------------------------
# Colab
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["response", "reply", "answer", "text", "message"])
async def test_colab_reads_any_reply_key(monkeypatch, key: str) -> None:
    """The notebook's reply key has varied across sessions."""
    monkeypatch.setenv("COLAB_URL", "https://example.ngrok-free.dev/")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={key: "colab says hi"})

    with mock_http(monkeypatch, handler):
        assert await ColabConnector().chat(MESSAGES) == "colab says hi"


async def test_colab_reads_openai_shape(monkeypatch) -> None:
    """An OpenAI-compatible notebook response is also understood."""
    monkeypatch.setenv("COLAB_URL", "https://example.ngrok-free.dev")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "nested"}}]})

    with mock_http(monkeypatch, handler):
        assert await ColabConnector().chat(MESSAGES) == "nested"


async def test_colab_uses_chat_endpoint_and_model(monkeypatch) -> None:
    """The connector posts to /chat with the notebook's model and no auth."""
    monkeypatch.setenv("COLAB_URL", "https://example.ngrok-free.dev")
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"response": "ok"})

    with mock_http(monkeypatch, handler):
        await ColabConnector().chat(MESSAGES)

    assert seen["url"] == "https://example.ngrok-free.dev/chat"
    assert seen["body"]["model"] == "llama3.1:8b"
    assert seen["auth"] is None


def test_colab_tracks_url_changes(monkeypatch) -> None:
    """A rotated ngrok URL is picked up on the next read."""
    monkeypatch.setenv("COLAB_URL", "https://first.ngrok-free.dev")
    connector = ColabConnector()
    assert connector.base_url() == "https://first.ngrok-free.dev"

    monkeypatch.setenv("COLAB_URL", "https://second.ngrok-free.dev/")
    assert connector.base_url() == "https://second.ngrok-free.dev"


async def test_colab_health_reports_hint_when_down(monkeypatch) -> None:
    """A dead tunnel produces an actionable message, not a bare error."""
    monkeypatch.setenv("COLAB_URL", "https://dead.ngrok-free.dev")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    connector = ColabConnector()
    with mock_http(monkeypatch, handler):
        assert await connector.health_check() is False

    status = await connector.status()
    assert status.state == "degraded"
    assert "ngrok" in status.detail


async def test_colab_health_ok(monkeypatch) -> None:
    """A running notebook reports healthy."""
    monkeypatch.setenv("COLAB_URL", "https://live.ngrok-free.dev")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/health")
        return httpx.Response(200, json={"status": "running"})

    with mock_http(monkeypatch, handler):
        assert await ColabConnector().health_check() is True


async def test_colab_is_not_rate_limited(monkeypatch) -> None:
    """Colab has no published quota, so the limiter must not block it."""
    monkeypatch.setenv("COLAB_URL", "https://example.ngrok-free.dev")
    connector = ColabConnector()
    assert connector.REQUESTS_PER_MINUTE == 0
    for _ in range(30):
        connector.rate_limit.record()
    assert connector.rate_limit.allows() is True


