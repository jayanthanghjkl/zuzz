"""Corpus Callosum — Bridge Support.

Provider bookkeeping for the cloud bridge: benching after hard failures,
candidate ordering, attempt plans and dashboard status. Split out of
`cloud_bridge.py` so both files stay under the 200-line limit.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from brain.corpus_callosum.connector_state import ProviderStatus
from brain.spinal_cord.stop_scope import CLOUD_PAUSE_REASON, KILL_SWITCH_ACTIVE

BENCH_SECONDS = 120


class BridgeSupport:
    """Selection and status mixin for :class:`~cloud_bridge.CloudBridge`.

    Expects the host class to provide ``connectors``, ``order``, ``_benched`` and
    ``configured_providers``.

    Attributes:
        kill_switch: The Spinal Cord switch consulted for cloud pauses. Held by
            reference rather than read from a global so the bridge and the gate
            cannot end up asking about different switches.
    """

    kill_switch: Any = None

    def bench(self, provider: str, seconds: int = BENCH_SECONDS) -> None:
        """Skip a provider for a while after a hard failure.

        Args:
            provider: Provider identifier.
            seconds: How long to skip it.
        """
        self._benched[provider] = time.time() + seconds  # type: ignore[attr-defined]

    def benched_for(self, provider: str) -> float:
        """Return seconds remaining before a provider is retried.

        Args:
            provider: Provider identifier.

        Returns:
            Seconds left on the bench, or 0 when it is not benched.
        """
        return max(0.0, round(self._benched.get(provider, 0.0) - time.time(), 1))  # type: ignore[attr-defined]

    def _candidate_order(self, preferred: str = None) -> List[str]:
        """Return configured, non-benched providers in attempt order.

        Args:
            preferred: Provider to move to the front when it is usable.

        Returns:
            Provider names in the order they should be tried.
        """
        candidates = [
            name
            for name in self.order  # type: ignore[attr-defined]
            if self.connectors[name].is_configured()  # type: ignore[attr-defined]
            and self.benched_for(name) <= 0
        ]
        if preferred and preferred in candidates:
            candidates.remove(preferred)
            candidates.insert(0, preferred)
        return candidates

    async def attempt_order(self) -> List[Dict[str, Any]]:
        """Return the providers to try, with the reason for any skips.

        Returns:
            One entry per provider: ``name``, ``usable`` and ``reason``.
        """
        plan: List[Dict[str, Any]] = []
        for name in self.order:  # type: ignore[attr-defined]
            connector = self.connectors[name]  # type: ignore[attr-defined]
            if not connector.is_configured():
                plan.append({"name": name, "usable": False, "reason": "not configured"})
            elif self.benched_for(name) > 0:  # type: ignore[attr-defined]
                plan.append(
                    {"name": name, "usable": False, "reason": f"benched {self.benched_for(name)}s"}  # type: ignore[attr-defined]
                )
            elif not connector.rate_limit.allows():
                plan.append({"name": name, "usable": False, "reason": "rate limited"})
            else:
                plan.append({"name": name, "usable": True, "reason": "ready"})
        return plan

    async def statuses(self) -> List[ProviderStatus]:
        """Return every provider's status.

        Returns:
            One ProviderStatus per configured connector.
        """
        return [await connector.status() for connector in self.connectors.values()]  # type: ignore[attr-defined]

    async def snapshot(self) -> Dict[str, Any]:
        """Return a dashboard-ready view of the whole bridge.

        Returns:
            Dict with configured providers, the attempt order, per-provider
            state and any active benches. While the kill switch is engaged every
            provider is flagged, because cloud agents cannot be stopped from here
            and the user needs to see that they are paused rather than idle.
        """
        paused = self.cloud_paused()
        providers = [status.to_dict() for status in await self.statuses()]
        if paused:
            for status in providers:
                status["state"] = KILL_SWITCH_ACTIVE
                status["detail"] = CLOUD_PAUSE_REASON
        return {
            "configured": self.configured_providers(),  # type: ignore[attr-defined]
            "attempt_order": await self.attempt_order(),
            "providers": providers,
            "benched": {name: self.benched_for(name) for name in self._benched},  # type: ignore[attr-defined]
            "kill_switch_active": paused,
        }

    def cloud_paused(self) -> bool:
        """Return True while the kill switch holds the cloud agents paused.

        Cloud agents cannot be terminated remotely, so the switch flags them and
        this flag is what the notebook reads on its next sync.
        """
        if self.kill_switch is None:
            from brain.spinal_cord.kill_switch import get_kill_switch

            return get_kill_switch().is_killed()
        return bool(self.kill_switch.is_killed())
