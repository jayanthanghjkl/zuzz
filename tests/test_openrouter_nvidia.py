"""Tests for the OpenRouter and NVIDIA Build connectors."""

from __future__ import annotations

import json

import httpx

from brain.corpus_callosum.groq_connector import GroqConnector
from brain.corpus_callosum.nvidia_connector import NvidiaConnector
from brain.corpus_callosum.openrouter_connector import OpenRouterConnector
from tests.provider_mocks import MESSAGES, mock_http, openai_reply


# OpenRouter and NVIDIA
# --------------------------------------------------------------------------


async def test_openrouter_sends_attribution_headers(monkeypatch) -> None:
    """OpenRouter receives the referer and title headers it asks for."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("http-referer")
        seen["title"] = request.headers.get("x-title")
        return openai_reply("or reply")(request)

    with mock_http(monkeypatch, handler):
        assert await OpenRouterConnector().chat(MESSAGES) == "or reply"

    assert seen["referer"] == "http://localhost:8080"
    assert "ZUZZ" in seen["title"]


def test_openrouter_defaults_to_a_free_model(monkeypatch) -> None:
    """The default OpenRouter model is a free-tier one."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert OpenRouterConnector().model_for().endswith(":free")


async def test_nvidia_chat(monkeypatch) -> None:
    """NVIDIA Build answers in the OpenAI format."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return openai_reply("nv reply")(request)

    with mock_http(monkeypatch, handler):
        assert await NvidiaConnector().chat(MESSAGES) == "nv reply"

    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["body"]["model"] == NvidiaConnector.DEFAULT_MODEL


def test_model_override_via_environment(monkeypatch) -> None:
    """Each connector honours its per-provider model override."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    assert GroqConnector().model_for() == "llama-3.1-70b-versatile"

