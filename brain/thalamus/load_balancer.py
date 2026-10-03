"""Thalamus — Load Balancer.

Picks which cloud provider to try next and keeps track of which ones are
currently failing, so one dead provider does not stall every request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.config import env, get_models

# How long a provider stays benched after a failure.
COOLDOWN_SECONDS = 120


@dataclass
class ProviderState:
    """Health bookkeeping for one cloud provider.

    Attributes:
        name: Provider identifier used by LiteLLM.
        failures: Consecutive failure count.
        benched_until: Unix timestamp until which the provider is skipped.
    """

    name: str
    failures: int = 0
    benched_until: float = 0.0


@dataclass
class Selection:
    """Chosen provider, or None when no provider is available.

    Attributes:
        provider: Provider name.
        skipped: Providers that were benched and therefore passed over.
        reason: Why this provider was chosen.
    """

    provider: Optional[str]
    skipped: List[str] = field(default_factory=list)
    reason: str = ""


class LoadBalancer:
    """Chooses among the free cloud providers declared in models.json."""

    def __init__(self, names: Optional[List[str]] = None) -> None:
        """Initialise provider state.

        Args:
            names: Providers to track. Defaults to every provider in
                `models.json` that has a credential in the environment. Pass the
                Corpus Callosum's configured providers so both agree on which
                cloud backends are usable.
        """
        self._states: Dict[str, ProviderState] = {
            name: ProviderState(name=name) for name in (names if names is not None else self._provider_names())
        }

    @staticmethod
    def _provider_names() -> List[str]:
        """Return provider names that have an API key configured."""
        return [
            name
            for name in get_models().get("fallback_order", [])
            if env(f"{name.upper()}_API_KEY") or env(f"{name.upper()}_API_URL")
        ]

    def select(self) -> Selection:
        """Pick the next healthy provider.

        Returns:
            A Selection. ``provider`` is None when every configured provider is
            currently benched, in which case the caller must fall back to local.
        """
        skipped: List[str] = []
        now = time.time()
        for name, state in self._states.items():
            if state.benched_until > now:
                skipped.append(name)
                continue
            return Selection(provider=name, skipped=skipped, reason="first healthy provider")
        return Selection(provider=None, skipped=skipped, reason="all providers benched")

    def report_failure(self, provider: str) -> None:
        """Record a provider failure and bench it for the cooldown window.

        Args:
            provider: The provider that just failed.
        """
        state = self._states.setdefault(provider, ProviderState(name=provider))
        state.failures += 1
        state.benched_until = time.time() + COOLDOWN_SECONDS

    def report_success(self, provider: str) -> None:
        """Clear the failure state for a provider.

        Args:
            provider: The provider that succeeded.
        """
        state = self._states.setdefault(provider, ProviderState(name=provider))
        state.failures = 0
        state.benched_until = 0.0

    def status(self) -> Dict[str, Dict[str, object]]:
        """Return a snapshot of provider health for the dashboard."""
        now = time.time()
        return {
            name: {
                "failures": state.failures,
                "benched": state.benched_until > now,
                "retry_in": max(0, int(state.benched_until - now)),
            }
            for name, state in self._states.items()
        }
