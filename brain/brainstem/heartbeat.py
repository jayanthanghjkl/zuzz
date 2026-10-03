"""Brainstem — Heartbeat.

Periodic health snapshot of the running agent: uptime, memory use, Ollama
reachability and loaded models. Used by the dashboard and the health check.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

import httpx

from brain.cerebrum.reasoning import list_local_models, ollama_host
from core.config import env_int


class Heartbeat:
    """Tracks agent uptime and reports system health."""

    def __init__(self, max_ram_mb: int = 0) -> None:
        """Initialise the heartbeat.

        Args:
            max_ram_mb: Agent RAM budget. Defaults to ``MAX_AGENT_RAM_MB``.
        """
        self.started_at = time.time()
        self.max_ram_mb = max_ram_mb or env_int("MAX_AGENT_RAM_MB", 3072)
        self.beats = 0

    def uptime_seconds(self) -> float:
        """Return how long the agent has been running, in seconds."""
        return round(time.time() - self.started_at, 1)

    def ram_mb(self) -> float:
        """Return this process's resident memory in MB.

        Returns:
            RSS in megabytes, or 0.0 when psutil is unavailable.
        """
        try:
            import psutil

            return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            return 0.0

    async def beat(self) -> Dict[str, Any]:
        """Take one health reading.

        Returns:
            Dict with uptime, RAM usage against budget, Ollama reachability and
            the list of locally available models.
        """
        self.beats += 1
        models: List[str] = await list_local_models()
        ram = self.ram_mb()
        return {
            "uptime_seconds": self.uptime_seconds(),
            "beats": self.beats,
            "ram_mb": ram,
            "ram_budget_mb": self.max_ram_mb,
            "ram_ok": ram <= self.max_ram_mb if ram else True,
            "ollama_url": ollama_host(),
            "ollama_up": bool(models),
            "local_models": models,
        }
