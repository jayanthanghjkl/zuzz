"""Corpus Callosum — Connector State.

Shared value objects for connectors: the error hierarchy, the sliding-window
rate limiter and the status record the dashboard renders. Split out of
`base_connector.py` to keep both files under the 200-line limit.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

HEALTHY = "healthy"
DEGRADED = "degraded"
OFFLINE = "offline"
RATE_LIMITED = "rate_limited"
UNCONFIGURED = "unconfigured"
UNTESTED = "untested"

WINDOW_SECONDS = 60.0


class ConnectorError(RuntimeError):
    """Raised when a provider call fails in a way the caller must handle."""


class RateLimitedError(ConnectorError):
    """Raised when the provider's request quota is exhausted."""


class NotConfiguredError(ConnectorError):
    """Raised when a provider is used without a credential."""


@dataclass
class RateLimit:
    """Sliding-window request limiter.

    Attributes:
        requests_per_minute: Quota, or 0 when the provider is unlimited.
        calls: Timestamps of requests inside the current window.
        benched_until: Unix timestamp until which the provider is skipped after
            the upstream returned HTTP 429.
    """

    requests_per_minute: int
    calls: Deque[float] = field(default_factory=deque)
    benched_until: float = 0.0

    def allows(self) -> bool:
        """Return True when another request fits inside the quota."""
        if self.is_benched():
            return False
        if self.requests_per_minute <= 0:
            return True
        self._trim()
        return len(self.calls) < self.requests_per_minute

    def is_benched(self) -> bool:
        """Return True while an upstream 429 cooldown is still active."""
        return self.benched_until > time.time()

    def bench(self, seconds: float = WINDOW_SECONDS) -> None:
        """Pause this provider after the upstream reported a rate limit.

        Args:
            seconds: How long to skip the provider.
        """
        self.benched_until = time.time() + seconds

    def record(self) -> None:
        """Register a request against the quota."""
        self._trim()
        self.calls.append(time.time())

    def used(self) -> int:
        """Return how many requests are counted in the current window."""
        self._trim()
        return len(self.calls)

    def retry_in(self) -> float:
        """Return seconds until the provider can be used again."""
        if self.is_benched():
            return round(self.benched_until - time.time(), 1)
        if not self.calls or self.requests_per_minute <= 0:
            return 0.0
        self._trim()
        if len(self.calls) < self.requests_per_minute:
            return 0.0
        return max(0.0, round(WINDOW_SECONDS - (time.time() - self.calls[0]), 1))

    def _trim(self) -> None:
        """Drop calls that have aged out of the window."""
        cutoff = time.time() - WINDOW_SECONDS
        while self.calls and self.calls[0] < cutoff:
            self.calls.popleft()


@dataclass
class ProviderStatus:
    """Health snapshot of one provider.

    Attributes:
        name: Provider identifier.
        state: One of the module state constants.
        detail: Short human-readable explanation.
        model: Default model in use.
        latency_ms: Last measured round-trip time.
        failure_count: Consecutive failures.
        requests_per_minute: Configured quota.
        requests_used: Requests in the current window.
    """

    name: str
    state: str = UNCONFIGURED
    detail: str = "no credential configured"
    model: str = ""
    latency_ms: Optional[float] = None
    failure_count: int = 0
    requests_per_minute: int = 0
    requests_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API."""
        return {
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "model": self.model,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "failure_count": self.failure_count,
            "requests_per_minute": self.requests_per_minute,
            "requests_used": self.requests_used,
        }
