"""Tests for the Corpus Callosum cloud bridge: selection, fallback, benching.

Providers are mocked with `httpx.MockTransport`, so no credentials are needed.
"""

from __future__ import annotations

import httpx
import pytest

from brain.corpus_callosum.cloud_bridge import CloudBridge
from brain.corpus_callosum.connector_state import ConnectorError
from brain.corpus_callosum.colab_connector import ColabConnector
from brain.corpus_callosum.groq_connector import GroqConnector
from brain.corpus_callosum.huggingface_connector import HuggingFaceConnector
from brain.corpus_callosum.nvidia_connector import NvidiaConnector
from brain.corpus_callosum.openrouter_connector import OpenRouterConnector
from tests.provider_mocks import (
    MESSAGES,
    SSE_BODY,
    clear_all_keys,
    mock_http,
    openai_reply,
)

ALL_CONNECTORS = (
    GroqConnector,
    ColabConnector,
    HuggingFaceConnector,
    OpenRouterConnector,
    NvidiaConnector,
)



# --------------------------------------------------------------------------
# Cloud bridge
# --------------------------------------------------------------------------


async def test_bridge_reports_nothing_configured(monkeypatch) -> None:
    """With no keys every provider is unconfigured."""
    clear_all_keys(monkeypatch, ALL_CONNECTORS)
    bridge = CloudBridge()
    assert bridge.configured_providers() == []
    snapshot = await bridge.snapshot()
    assert all(p["state"] == "unconfigured" for p in snapshot["providers"])


async def test_bridge_falls_back_to_second_provider(monkeypatch) -> None:
    """A failing provider hands the request to the next one."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.groq.com" in str(request.url):
            return httpx.Response(500, text="groq down")
        return openai_reply("from nvidia")(request)

    bridge = CloudBridge()
    with mock_http(monkeypatch, handler):
        result = await bridge.chat(MESSAGES)

    assert result["provider"] == "nvidia"
    assert result["reply"] == "from nvidia"
    assert result["attempts"] == 2
    assert any("groq" in error for error in result["errors"])


async def test_bridge_benches_failing_provider(monkeypatch) -> None:
    """A hard failure benches the provider so it is skipped next time."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.groq.com" in str(request.url):
            return httpx.Response(500, text="groq down")
        return openai_reply("ok")(request)

    bridge = CloudBridge()
    with mock_http(monkeypatch, handler):
        await bridge.chat(MESSAGES)

    assert bridge.benched_for("groq") > 0
    plan = {entry["name"]: entry for entry in await bridge.attempt_order()}
    assert plan["groq"]["usable"] is False
    assert "benched" in plan["groq"]["reason"]


async def test_bridge_raises_when_nothing_configured(monkeypatch) -> None:
    """No credentials means a clear error, not a hang."""
    clear_all_keys(monkeypatch, ALL_CONNECTORS)
    with pytest.raises(ConnectorError):
        await CloudBridge().chat(MESSAGES)


async def test_bridge_prefers_named_provider(monkeypatch) -> None:
    """A preferred provider is tried first when it is usable."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    with mock_http(monkeypatch, openai_reply("ok")):
        result = await CloudBridge().chat(MESSAGES, preferred="nvidia")

    assert result["provider"] == "nvidia"
    assert result["attempts"] == 1


async def test_bridge_streams_from_first_provider(monkeypatch) -> None:
    """Streaming picks the first usable provider and tags each fragment."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SSE_BODY, headers={"content-type": "text/event-stream"})

    events = []
    with mock_http(monkeypatch, handler):
        async for event in CloudBridge().stream(MESSAGES):
            events.append(event)

    assert "".join(event["content"] for event in events) == "hello"
    assert events[0]["provider"] == "groq"

