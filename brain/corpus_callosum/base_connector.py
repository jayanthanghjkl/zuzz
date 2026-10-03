"""Corpus Callosum — Base Connector.

Abstract contract every cloud backend implements. Provides the shared
machinery: credential detection, quota enforcement, failure tracking and the
status the dashboard renders.

A connector owns exactly one provider. Selection and fallback across
connectors belongs to :mod:`brain.corpus_callosum.cloud_bridge`.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from brain.corpus_callosum.connector_state import (
    ConnectorError,
    NotConfiguredError,
    RateLimit,
    RateLimitedError,
)
from brain.corpus_callosum.connector_status import ConnectorStatus
from brain.corpus_callosum.connector_stream import parse_sse_line
from brain.corpus_callosum.connector_transport import ConnectorTransport
from core.config import env


class BaseConnector(ConnectorTransport, ConnectorStatus, ABC):
    """Common behaviour for every cloud provider connector.

    Subclasses set the class attributes, implement :meth:`_chat_url`,
    :meth:`_payload` and :meth:`parse_chunk`, and inherit health checking,
    quota enforcement and status reporting.

    Attributes:
        NAME: Provider identifier, e.g. ``groq``.
        BASE_URL: API base URL.
        DEFAULT_MODEL: Model used when the caller does not pass one.
        API_KEY_ENV: Canonical environment variable holding the credential.
        API_KEY_ALIASES: Accepted alternative variable names.
        REQUESTS_PER_MINUTE: Free-tier quota, 0 when unlimited.
        TIMEOUT_SECONDS: Default request timeout.
        OPENAI_COMPATIBLE: True when the provider speaks the OpenAI chat schema.
        PUBLISHES_CATALOGUE: False when the provider has no ``/models``
            endpoint to check a model against.
    """

    NAME = "base"
    BASE_URL = ""
    DEFAULT_MODEL = ""
    API_KEY_ENV = ""
    API_KEY_ALIASES: tuple = ()
    REQUESTS_PER_MINUTE = 0
    TIMEOUT_SECONDS = 60
    OPENAI_COMPATIBLE = True
    PUBLISHES_CATALOGUE = True

    def __init__(self) -> None:
        """Initialise quota tracking and failure counters."""
        self.rate_limit = RateLimit(self.REQUESTS_PER_MINUTE)
        self.failures = 0
        self.last_latency_ms: Optional[float] = None
        self.last_error = ""
        # True once a call has actually come back. Distinguishes "working" from
        # "not asked yet", which a zero failure count cannot tell apart.
        self.answered = False

    @property
    def api_key(self) -> str:
        """Return the configured credential, or an empty string."""
        for name in (self.API_KEY_ENV, *self.API_KEY_ALIASES):
            value = env(name)
            if value:
                return value
        return ""

    def is_configured(self) -> bool:
        """Return True when this provider has the credential it needs."""
        return bool(self.api_key)

    def headers(self) -> Dict[str, str]:
        """Return default HTTP headers, including auth when available.

        Returns:
            Auth and content headers merged with any provider-specific extras.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers())
        return headers

    def extra_headers(self) -> Dict[str, str]:
        """Return provider-specific headers beyond auth.

        Returns:
            Headers merged into every request; empty by default.
        """
        return {}

    def model_for(self, model: Optional[str] = None) -> str:
        """Resolve which model to use.

        Args:
            model: Caller-supplied model, or None for the default.

        Returns:
            The model identifier.
        """
        return model or env(f"{self.NAME.upper()}_MODEL", self.DEFAULT_MODEL)

    def check_quota(self) -> None:
        """Raise if the connector cannot make a request right now.

        Raises:
            NotConfiguredError: If no credential is set.
            RateLimitedError: If the per-minute quota is used up.
        """
        if not self.is_configured():
            raise NotConfiguredError(f"{self.NAME}: {self.API_KEY_ENV} is not set")
        if not self.rate_limit.allows():
            raise RateLimitedError(
                f"{self.NAME}: rate limit reached, retry in {self.rate_limit.retry_in()}s"
            )

    def record_success(self, latency_ms: float) -> None:
        """Record a successful call and reset the failure streak.

        Args:
            latency_ms: Round-trip time in milliseconds.
        """
        self.rate_limit.record()
        self.failures = 0
        self.last_latency_ms = latency_ms
        self.last_error = ""
        self.answered = True

    def record_failure(self, error: str) -> None:
        """Record a failed call.

        Args:
            error: The error message, shown in the status detail.
        """
        self.failures += 1
        self.last_error = error

    def _chat_url(self, model: str) -> str:
        """Return the non-streaming endpoint.

        Args:
            model: Resolved model identifier.

        Returns:
            Absolute URL.
        """
        return f"{self.BASE_URL.rstrip('/')}/chat/completions"

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
        """Return the request body.

        Args:
            messages: Chat messages including the system prompt.
            model: Resolved model identifier.

        Returns:
            JSON body for the provider.
        """
        return {"model": model, "messages": messages, "stream": False, "temperature": 0.7}

    def extract_text(self, payload: Dict[str, Any]) -> str:
        """Pull the assistant text out of a response.

        Args:
            payload: Decoded JSON response.

        Returns:
            The assistant message text, or an empty string.
        """
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content") or "")

    def parse_chunk(self, line: str) -> str:
        """Extract text from one streamed line.

        Args:
            line: A single raw line from the stream.

        Returns:
            The text fragment, or an empty string to skip the line.
        """
        return parse_sse_line(line)
