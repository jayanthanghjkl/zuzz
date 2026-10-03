"""Tests for the cloud connectors themselves.

Every provider is exercised through `httpx.MockTransport`, so no network call,
API key or model download is needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from brain.corpus_callosum.base_connector import BaseConnector
from brain.corpus_callosum.colab_connector import ColabConnector
from brain.corpus_callosum.connector_state import (
    ConnectorError,
    NotConfiguredError,
    RateLimit,
    RateLimitedError,
)
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
# Rate limiter
# --------------------------------------------------------------------------


def test_rate_limit_blocks_over_quota() -> None:
    """Requests beyond the per-minute quota are refused."""
    limit = RateLimit(2)
    assert limit.allows() is True
    limit.record()
    limit.record()
    assert limit.allows() is False
    assert limit.used() == 2


def test_rate_limit_zero_is_unlimited() -> None:
    """A quota of zero means no limit."""
    limit = RateLimit(0)
    for _ in range(50):
        limit.record()
    assert limit.allows() is True


# --------------------------------------------------------------------------
# Connector contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ALL_CONNECTORS)
def test_every_connector_implements_the_contract(cls) -> None:
    """Each provider satisfies the BaseConnector interface."""
    connector = cls()
    assert isinstance(connector, BaseConnector)
    assert connector.NAME
    for method in ("chat", "stream_chat", "health_check", "get_rate_limit_status", "status"):
        assert callable(getattr(connector, method))


@pytest.mark.parametrize("cls", ALL_CONNECTORS)
def test_unconfigured_connector_refuses_calls(cls, monkeypatch) -> None:
    """A connector with no credential must not attempt a request."""
    clear_all_keys(monkeypatch, ALL_CONNECTORS)
    with pytest.raises(NotConfiguredError):
        cls().check_quota()


@pytest.mark.parametrize("cls", ALL_CONNECTORS)
async def test_unconfigured_connector_reports_status(cls, monkeypatch) -> None:
    """Status reports unconfigured rather than raising."""
    clear_all_keys(monkeypatch, ALL_CONNECTORS)
    status = await cls().status()
    assert status.state == "unconfigured"


async def test_never_called_connector_is_not_reported_healthy(monkeypatch) -> None:
    """A configured provider that has not answered is 'untested', not 'healthy'.

    A zero failure count is not evidence of health — it also describes a
    provider nobody has tried yet, which may well be down.
    """
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    status = await GroqConnector().status()
    assert status.state == "untested"
    assert status.state != "healthy"


async def test_successful_call_marks_the_provider_healthy(monkeypatch) -> None:
    """Once a call comes back, the provider is reported healthy."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    connector = GroqConnector()
    connector.record_success(120.0)
    status = await connector.status()
    assert status.state == "healthy"


async def test_failed_call_downgrades_from_untested_to_degraded(monkeypatch) -> None:
    """Evidence of failure replaces the absence of evidence."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    connector = GroqConnector()
    connector.record_failure("boom")
    status = await connector.status()
    assert status.state == "degraded"


