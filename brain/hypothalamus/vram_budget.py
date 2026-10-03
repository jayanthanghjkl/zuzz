"""Hypothalamus — VRAM budget enforcement.

The budget is a safety guard, not a performance hint. Running out of VRAM midway
through inference crashes the model and can take down the GPU driver, which affects
the whole system — not just ZUZZ. The budget must therefore be a hard cap, enforced
before a load, with headroom the OS compositor and desktop can always reach.

The budget reads its totals from ``config/resource_limits.json`` and from the
``MAX_VRAM_MB`` / ``VRAM_HEADROOM_MB`` environment variables, with the config as
the fallback default. Every number that affects the decision — total budget,
headroom, current allocation — is a constructor argument overridable in tests,
so the hard cap can be exercised without an NVIDIA card.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import env_int, get_resource_limits


class VRAMBudget:
    """Enforces the VRAM allocation budget.

    Attributes:
        total_mb: The hard cap on model VRAM allocations.
        headroom_mb: Reserved for the OS and desktop; never allocated to models.
        _allocated: Mapping of model name to MB currently assigned.
    """

    def __init__(
        self,
        total_mb: Optional[int] = None,
        headroom_mb: Optional[int] = None,
        allocated: Optional[Dict[str, float]] = None,
    ) -> None:
        """Load the budget from config or constructor.

        Args:
            total_mb: Override for the total budget in MB.
            headroom_mb: Override for the reserved headroom in MB.
            allocated: Seed allocations, for tests.
        """
        config = get_resource_limits().get("vram", {})
        env_total = env_int("MAX_VRAM_MB", 0)
        self.total_mb = total_mb if total_mb is not None else (
            env_total or int(config.get("total_budget_mb", 4608))
        )
        env_headroom = env_int("VRAM_HEADROOM_MB", 0)
        self.headroom_mb = headroom_mb if headroom_mb is not None else (
            env_headroom or int(config.get("reserved_headroom_mb", 1500))
        )
        self._allocated: Dict[str, float] = dict(allocated or {})

    @property
    def allocated_mb(self) -> float:
        """Return the total MB currently assigned to models."""
        return round(sum(self._allocated.values()), 1)

    @property
    def usable_mb(self) -> float:
        """Return the MB available for models (total minus headroom).

        The safe direction: if the headroom alone exceeds the total, zero is
        available rather than a negative number that might be misread as free.
        """
        return max(0.0, self.total_mb - self.headroom_mb)

    def available_for_load(self) -> float:
        """Return the MB free for new models right now.

        Returns:
            ``usable_mb - allocated_mb``, clamped to zero.
        """
        return max(0.0, self.usable_mb - self.allocated_mb)

    def allocated_to(self, model: str) -> float:
        """Return the MB currently assigned to one model.

        Args:
            model: The model name.

        Returns:
            Assigned MB, or 0.0 when the model is not tracked.
        """
        return round(self._allocated.get(model, 0.0), 1)

    def assign(self, model: str, vram_mb: float) -> None:
        """Record that a model is using ``vram_mb`` of VRAM.

        Args:
            model: The model name.
            vram_mb: The model's VRAM footprint in MB.
        """
        self._allocated[model] = round(vram_mb, 1)

    def release(self, model: str) -> None:
        """Release the VRAM tracked for one model.

        Args:
            model: The model name. If unknown, this is a no-op.
        """
        self._allocated.pop(model, None)

    def can_fit(self, model_vram_mb: float) -> bool:
        """Return True when a model fits within the remaining budget.

        Args:
            model_vram_mb: The model's VRAM requirement in MB.

        Returns:
            True when ``model_vram_mb`` fits in the space left after headroom
            and existing allocations.
        """
        if model_vram_mb > self.total_mb:
            return False
        return self.available_for_load() >= model_vram_mb

    def pressure_tier(self) -> str:
        """Return which configured pressure tier the current usage sits in.

        The tiers are the ones in ``config/resource_limits.json``. Reporting the
        tier alongside the raw percentage means the dashboard colours the same
        bands the eviction logic acts on, so what the operator sees and what the
        machine does cannot drift apart.

        Returns:
            ``"normal"``, ``"warning"``, ``"evicting"``, ``"emergency"`` or
            ``"panic"``.
        """
        if not self.total_mb:
            return "normal"
        limits = get_resource_limits().get("vram", {})
        used = self.allocated_mb / self.total_mb * 100
        for tier, key, fallback in (
            ("panic", "panic_percent", 98),
            ("emergency", "emergency_cloud_percent", 95),
            ("evicting", "auto_unload_percent", 85),
            ("warning", "warn_percent", 75),
        ):
            if used >= float(limits.get(key, fallback)):
                return tier
        return "normal"

    def forecast_after_load(
        self, model_vram_mb: float
    ) -> Dict[str, Any]:
        """Return the projected state after loading one model.

        Args:
            model_vram_mb: The model's VRAM requirement in MB.

        Returns:
            ``total_mb``, ``headroom_mb``, ``allocated_after``, ``available_after``,
            ``fits`` and ``percent_used`` (allocated / total).
        """
        allocated = self.allocated_mb + model_vram_mb
        available = self.total_mb - self.headroom_mb - allocated
        return {
            "total_mb": self.total_mb,
            "headroom_mb": self.headroom_mb,
            "allocated_after": round(allocated, 1),
            "available_after": round(max(0.0, available), 1),
            "fits": available >= 0,
            "percent_used": round(allocated / self.total_mb * 100, 1) if self.total_mb else 0.0,
        }
