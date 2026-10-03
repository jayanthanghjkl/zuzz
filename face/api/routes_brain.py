"""Face API — brain status routes.

Health, heartbeat and routing introspection for the dashboard.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from brain.cerebrum.reasoning import ollama_host

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> Dict[str, Any]:
    """Return a liveness probe.

    Args:
        request: Used to reach the shared agent components.

    Returns:
        A minimal status payload that never fails the request.
    """
    return {"status": "ok", "version": request.app.version}


@router.get("/api/brain/status")
async def brain_status(request: Request) -> Dict[str, Any]:
    """Return heartbeat, router and local model information.

    Args:
        request: Used to reach the shared agent components.

    Returns:
        Combined status of the heart, thalamus and local Ollama.
    """
    beat = await request.app.state.heartbeat.beat()
    return {
        "heartbeat": beat,
        "thalamus": request.app.state.thalamus.status(),
        "process_manager": request.app.state.process_manager.status(),
        "scheduler": request.app.state.scheduler.status(),
        "kill_switch": request.app.state.kill_switch.status(),
        "ollama_url": ollama_host(),
        "recent_activity": request.app.state.logger.tail(10),
    }


@router.get("/api/providers")
async def providers(request: Request) -> Dict[str, Any]:
    """Return the health of every cloud provider connector.

    Args:
        request: Used to reach the Corpus Callosum bridge.

    Returns:
        Configured providers, the attempt order and per-provider state
        (healthy, degraded, offline, rate_limited or unconfigured).
    """
    snapshot = await request.app.state.thalamus.cerebrum.bridge.snapshot()
    snapshot["last_provider"] = request.app.state.thalamus.cerebrum.last_provider
    return snapshot


@router.get("/api/brain/activity")
async def activity(request: Request, count: int = 20) -> Dict[str, Any]:
    """Return recent activity log records.

    Args:
        request: Used to reach the shared agent components.
        count: How many records to return, capped at 200.

    Returns:
        The tail of the immutable activity log.
    """
    return {"entries": request.app.state.logger.tail(max(1, min(count, 200)))}
