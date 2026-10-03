"""Tests for model-liveness detection.

Uses mocked catalogues so no provider is contacted.
"""

from __future__ import annotations

import httpx
import pytest

from brain.corpus_callosum.cloud_bridge import CloudBridge
from brain.corpus_callosum.colab_connector import ColabConnector
from brain.corpus_callosum.connector_state import ConnectorError
from brain.corpus_callosum.groq_connector import GroqConnector
from brain.corpus_callosum.huggingface_connector import HuggingFaceConnector
from brain.corpus_callosum.openrouter_connector import OpenRouterConnector
from scripts.check_models import DEAD, OK, SKIPPED, UNKNOWN, check_connector, matches

CATALOGUE = {"data": [{"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3.8-27b"}]}


# --------------------------------------------------------------------------
# Catalogue listing
# --------------------------------------------------------------------------


async def test_list_models_parses_openai_catalogue(monkeypatch) -> None:
    """The OpenAI-style `data[].id` shape is parsed."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    connector = GroqConnector()

    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(200, json=CATALOGUE)):
        assert await connector.list_models() == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]


async def test_list_models_unconfigured_is_empty(monkeypatch) -> None:
    """An unconfigured provider lists nothing."""
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert await OpenRouterConnector().list_models() == []


async def test_list_models_raises_on_http_error(monkeypatch) -> None:
    """A failing catalogue request raises, so the caller can tell it from empty."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(500, text="boom")):
        with pytest.raises(ConnectorError, match="HTTP 500"):
            await GroqConnector().list_models()


async def test_huggingface_checks_repo_existence(monkeypatch) -> None:
    """HuggingFace has no catalogue, so the repo itself is checked."""
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf-test")
    connector = HuggingFaceConnector()

    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(200, json={"id": "x"})):
        assert await connector.list_models() == [connector.model_for()]

    with mock_http(monkeypatch, lambda r: httpx.Response(404, json={})):
        assert await connector.list_models() == []


async def test_colab_publishes_no_catalogue() -> None:
    """Colab cannot be checked, and says so rather than claiming a model is fine."""
    assert await ColabConnector().list_models() == []


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_matches_exact_and_namespaced() -> None:
    """A namespaced config matches a bare catalogue entry and vice versa."""
    assert matches("openai/gpt-oss-120b", ["openai/gpt-oss-120b"]) is True
    assert matches("meta-llama/llama-3.1-8b-instruct", ["llama-3.1-8b-instruct"]) is True
    assert matches("llama-3.1-8b-instruct", ["meta-llama/llama-3.1-8b-instruct"]) is True


def test_matches_rejects_unrelated() -> None:
    """An unrelated catalogue entry is not a match."""
    assert matches("openai/gpt-oss-120b", ["qwen/qwen3.8-27b"]) is False


# --------------------------------------------------------------------------
# check_connector outcomes
# --------------------------------------------------------------------------


async def test_check_reports_ok_when_model_is_served(monkeypatch) -> None:
    """A live model reports ok."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(200, json=CATALOGUE)):
        report = await check_connector(GroqConnector())
    assert report.state == OK
    assert report.catalogue_size == 2


async def test_check_reports_dead_when_model_is_gone(monkeypatch) -> None:
    """A retired model reports dead, with a suggestion."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from tests.provider_mocks import mock_http

    retired = {"data": [{"id": "openai/gpt-oss-20b"}, {"id": "qwen/qwen3.8-27b"}]}
    with mock_http(monkeypatch, lambda r: httpx.Response(200, json=retired)):
        report = await check_connector(GroqConnector())
    assert report.state == DEAD
    assert "not in catalogue" in report.detail


async def test_check_reports_unknown_when_catalogue_unreachable(monkeypatch) -> None:
    """An unreachable catalogue is unknown, never dead."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(500, text="boom")):
        report = await check_connector(GroqConnector())
    assert report.state == UNKNOWN
    assert "unreachable" in report.detail or "HTTP 500" in report.detail


async def test_check_reports_dead_when_catalogue_is_empty(monkeypatch) -> None:
    """A reachable provider serving no models means the model is gone."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(200, json={"data": []})):
        report = await check_connector(GroqConnector())
    assert report.state == DEAD


async def test_check_reports_dead_when_hf_repo_is_gone(monkeypatch) -> None:
    """A deleted HuggingFace repo is dead, not merely unverified."""
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf-test")
    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(404, json={})):
        report = await check_connector(HuggingFaceConnector())
    assert report.state == DEAD


async def test_check_reports_unknown_when_hf_repo_is_gated(monkeypatch) -> None:
    """A gated repo is a permission problem, not a removed model."""
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf-test")
    from tests.provider_mocks import mock_http

    with mock_http(monkeypatch, lambda r: httpx.Response(401, json={})):
        report = await check_connector(HuggingFaceConnector())
    assert report.state == UNKNOWN
    assert "gated" in report.detail


async def test_check_skips_unconfigured_provider(monkeypatch) -> None:
    """A provider with no key is skipped without a network call."""
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    report = await check_connector(OpenRouterConnector())
    assert report.state == SKIPPED


async def test_check_colab_uses_health_not_catalogue(monkeypatch) -> None:
    """Colab is judged by its health endpoint, never assumed healthy."""
    monkeypatch.setenv("COLAB_API_URL", "https://x.ngrok-free.dev")

    from tests.provider_mocks import mock_http

    ok = httpx.Response(200, json={"flask": "running", "ollama_process": "alive", "overall": "healthy"})
    with mock_http(monkeypatch, lambda r: ok):
        report = await check_connector(ColabConnector())
    assert report.state == UNKNOWN
    assert "flask running" in report.detail

    bad = httpx.Response(200, json={"overall": "degraded"})
    with mock_http(monkeypatch, lambda r: bad):
        report = await check_connector(ColabConnector())
    assert report.state == DEAD


def test_bridge_order_matches_configured_list() -> None:
    """The bridge exposes one connector per fallback-order entry."""
    bridge = CloudBridge()
    assert bridge.order == ["groq", "colab", "huggingface", "openrouter", "nvidia"]
