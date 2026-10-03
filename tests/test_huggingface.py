"""Tests for the HuggingFace connector, whose API is not OpenAI-compatible."""

from __future__ import annotations

import json

import httpx

from brain.corpus_callosum.huggingface_connector import HuggingFaceConnector
from tests.provider_mocks import mock_http


# --------------------------------------------------------------------------
# HuggingFace
# --------------------------------------------------------------------------


async def test_huggingface_flattens_messages(monkeypatch) -> None:
    """The message list is flattened into the inference API's schema."""
    monkeypatch.setenv("HF_API_KEY", "hf-test")
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"generated_text": "hf reply"}])

    with mock_http(monkeypatch, handler):
        reply = await HuggingFaceConnector().chat(
            [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
            ]
        )

    assert reply == "hf reply"
    assert seen["url"].endswith("/meta-llama/Meta-Llama-3.1-8B-Instruct")
    assert "System: be brief" in seen["body"]["inputs"]
    assert "User: hello" in seen["body"]["inputs"]


def test_huggingface_accepts_alias_key(monkeypatch) -> None:
    """`HF_API_KEY` works as well as the canonical name."""
    monkeypatch.setenv("HF_API_KEY", "hf-test")
    assert HuggingFaceConnector().is_configured() is True


def test_huggingface_chunk_parsing() -> None:
    """HuggingFace's event stream is parsed into fragments."""
    connector = HuggingFaceConnector()
    assert connector.parse_chunk('data: "hello"') == "hello"
    assert connector.parse_chunk("data: [DONE]") == ""
    assert connector.parse_chunk("ignore me") == ""


