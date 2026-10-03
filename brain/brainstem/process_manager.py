"""Brainstem — Process Manager.

Thin wrapper over the Ollama HTTP API. Loads and unloads models on demand so
the VRAM budget in PROJECT.md section 3 is respected: only the fast model stays
resident, heavier models are evicted after a timeout.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import httpx

from brain.cerebrum.reasoning import list_local_models, ollama_host
from core.config import env_int


class ProcessManager:
    """Keeps local models loaded on demand and unloads idle ones.

    Attributes:
        unload_after: Seconds of inactivity before a model is unloaded.
    """

    def __init__(self) -> None:
        """Initialise the manager with the configured unload timeout."""
        self.unload_after = env_int("MODEL_UNLOAD_SECONDS", 60)
        self._keep_resident: List[str] = []
        self._loaded_at: Dict[str, float] = {}
        #: Optional Hypothalamus. Attached by the composition root so the reaper
        #: knows what the VRAM budget actually looks like instead of unloading on
        #: a timer alone. Left as None it behaves exactly as before.
        self.hypothalamus: Any = None

    async def keep_resident(self, model: str) -> None:
        """Mark a model as always-loaded so it is never evicted.

        Args:
            model: Ollama model name, e.g. ``qwen2.5-coder:1.5b``.
        """
        if model and model not in self._keep_resident:
            self._keep_resident.append(model)

    async def models(self) -> List[str]:
        """List models Ollama has downloaded."""
        return await list_local_models()

    async def mark_loaded(self, model: str) -> None:
        """Record that a model is now resident, starting its idle clock.

        The idle clock has to start when the model *arrives*, and only the code
        that loads it knows that. This existed implicitly — the reaper read
        ``_loaded_at`` and nothing ever wrote it, so the sweep had nothing to
        consider and the dashboard had nothing to show.

        Safe to call repeatedly: a re-load resets the clock, which is the correct
        behaviour, because the model was genuinely evicted and fetched again.

        Args:
            model: Ollama model name.
        """
        if model:
            self._loaded_at[model] = time.time()

    async def unload(self, model: str, keep_alive: int = 0) -> bool:
        """Unload one model from VRAM.

        Args:
            model: Ollama model name.
            keep_alive: Passed to Ollama; 0 unloads immediately.

        Returns:
            True when Ollama accepted the request.
        """
        payload = {"model": model, "keep_alive": keep_alive}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{ollama_host()}/api/generate", json=payload)
                self._loaded_at.pop(model, None)
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def unload_idle(self) -> List[str]:
        """Unload every on-demand model idle for longer than the timeout.

        VRAM-aware since Step 6: with a Hypothalamus attached the sweep is
        delegated to the model manager, which knows both the VRAM budget and each
        model's real load time. Two things that changes:

        * **On-demand models are left alone while the budget is comfortable.**
          Unloading on a timer alone churns VRAM: the next request pays the load
          again, and a load is the expensive direction. A model that is idle but
          comfortably inside the budget costs nothing and saves a load.
        * **Eviction takes pressure into account**, because under pressure an idle
          model is actually costing something.

        Without a Hypothalamus this falls back to the original timer-only sweep,
        so the Brainstem does not require the resource layer in order to run.

        Returns:
            The models that were unloaded.
        """
        if self.hypothalamus is not None:
            unloaded = await self.hypothalamus.models.unload_idle(
                idle_seconds=self.unload_after
            )
            # Every model the manager unloaded, not just the first: a sweep that
            # forgets its third entry will unload the same model again next tick.
            for model in unloaded:
                self._loaded_at.pop(model, None)
            return unloaded
        now = time.time()
        unloaded: List[str] = []
        for model, loaded_at in list(self._loaded_at.items()):
            if model in self._keep_resident:
                continue
            if now - loaded_at >= self.unload_after:
                if await self.unload(model):
                    unloaded.append(model)
        return unloaded

    async def reap_loop(self, interval: int = 15) -> None:
        """Run :meth:`unload_idle` forever at ``interval`` seconds.

        Args:
            interval: Seconds between sweeps.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                await self.unload_idle()
            except Exception:  # never let the reaper kill the agent
                continue

    def status(self) -> Dict[str, Any]:
        """Return model bookkeeping for the dashboard.

        Reads the model manager when one is attached rather than a second copy of
        the state held here. Two dictionaries tracking the same models is a
        design that guarantees they disagree, and until this was fixed the local
        one was never written at all — so the dashboard reported an empty machine
        while two models sat in VRAM. When nothing is attached there is nothing
        to report, which is the honest answer rather than a fabricated empty set.
        """
        now = time.time()
        if self.hypothalamus is not None:
            entries = self.hypothalamus.models.loaded_models()
            return {
                "unload_after_seconds": self.unload_after,
                "resident": list(self._keep_resident),
                "loaded": {
                    entry["model"]: {
                        "idle_seconds": entry["idle_seconds"],
                        "vram_mb": entry["vram_mb"],
                        "priority": entry["priority"],
                    }
                    for entry in entries
                },
                "source": "model_manager",
            }
        return {
            "unload_after_seconds": self.unload_after,
            "resident": list(self._keep_resident),
            "loaded": {
                model: {"idle_seconds": round(now - ts)} for model, ts in self._loaded_at.items()
            },
            "source": "local",
        }
