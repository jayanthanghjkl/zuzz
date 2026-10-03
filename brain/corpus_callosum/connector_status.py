"""Corpus Callosum — Connector Status.

Status and quota reporting mixin. Split out of `connector_transport.py` so both
files stay under the 200-line limit.
"""

from __future__ import annotations

from typing import Any, Dict

from brain.corpus_callosum.connector_state import (
    DEGRADED,
    HEALTHY,
    OFFLINE,
    RATE_LIMITED,
    UNTESTED,
    ProviderStatus,
)


class ConnectorStatus:
    """Health and quota reporting for connectors.

    Expects the host class to provide ``is_configured``, ``model_for``,
    ``health_url``, ``headers`` and the ``rate_limit``, ``failures``,
    ``last_latency_ms`` and ``last_error`` attributes from ``BaseConnector``.
    """

    async def health_check(self) -> bool:
        """Check whether the provider is reachable.

        Returns:
            True when configured and reachable.
        """
        if not self.is_configured():  # type: ignore[attr-defined]
            return False
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self.health_url(), headers=self.headers()  # type: ignore[attr-defined]
                )
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    def health_url(self) -> str:
        """Return the endpoint used for the health probe."""
        return f"{self.BASE_URL.rstrip('/')}/models"  # type: ignore[attr-defined]

    async def get_rate_limit_status(self) -> Dict[str, Any]:
        """Return the current quota usage.

        Returns:
            Provider name, quota, usage and seconds until retry.
        """
        limit = self.rate_limit  # type: ignore[attr-defined]
        return {
            "provider": self.NAME,  # type: ignore[attr-defined]
            "limit": limit.requests_per_minute,
            "used": limit.used(),
            "retry_in_seconds": limit.retry_in(),
        }

    async def status(self) -> ProviderStatus:
        """Return the provider's health for the dashboard.

        Returns:
            A ProviderStatus reflecting configuration, quota and the recorded
            failure streak.
        """
        limit = self.rate_limit  # type: ignore[attr-defined]
        model = self.model_for()  # type: ignore[attr-defined]
        if not self.is_configured():  # type: ignore[attr-defined]
            return ProviderStatus(name=self.NAME, model=model)  # type: ignore[attr-defined]
        if not limit.allows():
            return ProviderStatus(
                name=self.NAME,  # type: ignore[attr-defined]
                state=RATE_LIMITED,
                detail=f"retry in {limit.retry_in()}s",
                model=model,
                requests_per_minute=limit.requests_per_minute,
                requests_used=limit.used(),
            )

        failures = int(self.failures)  # type: ignore[attr-defined]
        state, detail = HEALTHY, "ok"
        if failures >= 2:
            state, detail = OFFLINE, self.last_error or "repeated failures"  # type: ignore[attr-defined]
        elif failures == 1:
            state, detail = DEGRADED, self.last_error or "last call failed"  # type: ignore[attr-defined]
        elif not getattr(self, "answered", False):
            # Zero failures is not evidence of health: it also means nothing has
            # been asked yet. Reporting "ok" here would show a green dot for a
            # provider that may well be down, which is the exact failure the
            # dashboard exists to reveal.
            state, detail = UNTESTED, "configured but not called yet"
        return ProviderStatus(
            name=self.NAME,  # type: ignore[attr-defined]
            state=state,
            detail=detail,
            model=model,
            latency_ms=self.last_latency_ms,  # type: ignore[attr-defined]
            failure_count=failures,
            requests_per_minute=limit.requests_per_minute,
            requests_used=limit.used(),
        )
