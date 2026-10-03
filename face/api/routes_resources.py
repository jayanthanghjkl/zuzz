"""Face API — resource and model management routes.

The interface to the Hypothalamus: live gauges for every sensor, and the load /
unload / swap controls for the models ZUZZ is holding in VRAM.

The permission flow lives in :mod:`face.api.resource_confirmations`, mirroring
``file_confirmations`` for the file layer. These routes therefore do two things
only: read a sensor, or hand a model-management call to the manager once
:func:`~face.api.resource_confirmations.authorise` has approved it. Nothing here
decides anything, so there is no version of this file in which a forgotten
permission check would reach the hardware.

Reading resources is not gated at all. Showing how much memory is free is the
same kind of read as showing the trust level, and a gauge that needed a
permission would be a gauge nobody looked at.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from brain.hypothalamus.hypothalamus import Hypothalamus
from face.api.resource_confirmations import authorise, refuse

router = APIRouter()

#: How often the live socket publishes. 1Hz matches the chart, and is slow enough
#: that a browser left open all day does not become a load generator.
LIVE_INTERVAL_SECONDS = 1.0


class ModelRequest(BaseModel):
    """A request to load or unload one model."""

    model: str = Field(..., description="Ollama model name")
    confirm_id: str = Field("", description="the confirmation this request answers")


class HotSwapRequest(BaseModel):
    """A request to replace one model with another."""

    old_model: str = Field("", description="the model to replace, empty for a cold load")
    new_model: str = Field(..., description="the model to load")
    confirm_id: str = Field("", description="the confirmation this request answers")


def _brain(request: Request) -> Hypothalamus:
    """Return the Hypothalamus wired into the app."""
    return request.app.state.hypothalamus


async def _mutate(
    request: Request, operation: str, model: str, confirm_id: str, call: Any
) -> Dict[str, Any]:
    """Authorise, then run one model-management coroutine.

    Args:
        request: Used to reach the app's components.
        operation: What is being done.
        model: The model being acted on.
        confirm_id: The confirmation this request answers, or empty.
        call: Awaitable factory taking the decision.

    Returns:
        The parked confirmation, or ``{"status": "ok", "result": ...}``.

    Raises:
        HTTPException: Whatever the permission check or the manager raised.
    """
    decision = authorise(request, operation, model, confirm_id)
    if isinstance(decision, dict):
        return decision
    try:
        result = await call(decision)
    except Exception as exc:
        raise refuse(exc) from exc
    return {"status": "ok", "result": result}


@router.get("/api/resources/ram")
async def read_ram(request: Request) -> Dict[str, Any]:
    """Return system RAM and this agent's share of it."""
    monitor = _brain(request).monitor
    return {"ram": monitor.get_ram(), "agent_ram": monitor.get_agent_ram()}


@router.get("/api/resources/vram")
async def read_vram(request: Request) -> Dict[str, Any]:
    """Return VRAM, the budget, and whether the figures are measured or estimated.

    The ``reporting`` field matters as much as the numbers: on a machine with no
    working sensor the budget is an estimate charged at 1.5x, and a gauge showing
    that in the same typeface as a measured reading would be lying by omission.
    """
    brain = _brain(request)
    return {
        "vram": brain.monitor.get_vram(),
        "budget": {
            "total_mb": brain.budget.total_mb,
            "headroom_mb": brain.budget.headroom_mb,
            "usable_mb": brain.budget.usable_mb,
            "allocated_mb": brain.budget.allocated_mb,
            "available_mb": brain.budget.available_for_load(),
            "percent_used": brain.models.percent_used(),
        },
        "reporting": brain.models.reporting_mode(),
    }


@router.get("/api/resources/cpu")
async def read_cpu(request: Request) -> Dict[str, Any]:
    """Return CPU usage overall and per core."""
    return {"cpu": _brain(request).monitor.get_cpu()}


@router.get("/api/resources/gpu")
async def read_gpu(request: Request) -> Dict[str, Any]:
    """Return GPU utilization, temperature and power.

    ``temperature_c`` is null rather than zero on a machine whose sensor cannot
    be read, so the dashboard can say the reading is missing instead of showing a
    confidently cold GPU.
    """
    return {"gpu": _brain(request).monitor.get_gpu()}


@router.get("/api/resources/disk")
async def read_disk(request: Request) -> Dict[str, Any]:
    """Return disk usage for the project root."""
    return {"disk": _brain(request).monitor.get_disk()}


@router.get("/api/resources/snapshot")
async def read_snapshot(request: Request) -> Dict[str, Any]:
    """Return every sensor in one call, plus the region-wide status."""
    brain = _brain(request)
    return {"snapshot": brain.snapshot(), "status": brain.get_status()}


@router.websocket("/api/resources/live")
async def live_resources(websocket: WebSocket) -> None:
    """Stream resource snapshots to the dashboard once a second.

    The socket carries readings only. Nothing published here can load a model or
    change a power mode, which is what makes it safe to leave open.

    Args:
        websocket: The connection to publish on.
    """
    await websocket.accept()
    brain: Hypothalamus = websocket.app.state.hypothalamus

    async def publish(snapshot: Dict[str, Any]) -> None:
        """Send one snapshot to the client."""
        await websocket.send_json(snapshot)

    unsubscribe = brain.subscribe(publish)
    try:
        while True:
            await websocket.send_json(brain.snapshot())
            await asyncio.sleep(LIVE_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        pass
    except Exception:
        # A closed socket is the ordinary end of this loop, not an error worth
        # reporting. The subscription is removed either way, so a client that
        # reconnects cannot leak a callback per attempt.
        pass
    finally:
        unsubscribe()


@router.get("/api/models/loaded")
async def list_loaded(request: Request) -> Dict[str, Any]:
    """Return the loaded models with their VRAM cost and idle time."""
    manager = _brain(request).models
    return {
        "loaded": manager.loaded_models(),
        "known": manager.known_models(),
        "reporting": manager.reporting_mode(),
        "cloud_only": manager.cloud_only,
    }


@router.get("/api/models/can-fit")
async def can_fit(request: Request, model: str) -> Dict[str, Any]:
    """Return whether one model may be loaded, and why.

    Answers rather than raises, because the caller is usually the Thalamus
    deciding between local and cloud, and it routes better with a reason than
    with an exception.
    """
    return _brain(request).models.can_load(model)


@router.post("/api/models/load")
async def load_model(request: Request, body: ModelRequest) -> Dict[str, Any]:
    """Load a model into VRAM, through the gate.

    Raises:
        HTTPException: 400 for a bad confirmation id; 409 when a confirmation is
            needed or declined; 429 when the budget is full; 404 for an unknown
            model; 503 when Ollama refused or timed out.
    """
    manager = _brain(request).models
    return await _mutate(
        request, "load", body.model, body.confirm_id,
        lambda decision: manager.load(body.model, decision),
    )


@router.post("/api/models/unload")
async def unload_model(request: Request, body: ModelRequest) -> Dict[str, Any]:
    """Unload a model, through the gate.

    Raises:
        HTTPException: 409 when a confirmation is needed, declined or blocked, or
            when the model is always-loaded and the trust level is too low.
    """
    manager = _brain(request).models
    return await _mutate(
        request, "unload", body.model, body.confirm_id,
        lambda decision: manager.unload(body.model, decision),
    )


@router.post("/api/models/hot-swap")
async def hot_swap(request: Request, body: HotSwapRequest) -> Dict[str, Any]:
    """Replace one model with another, atomically.

    Raises:
        HTTPException: 429 when the budget is full; 503 when the new model failed
            and the old one was kept loaded.
    """
    manager = _brain(request).models
    operation = f"hot-swap {body.old_model}->{body.new_model}"
    return await _mutate(
        request, operation, body.new_model, body.confirm_id,
        lambda decision: manager.hot_swap(body.old_model, body.new_model, decision),
    )


@router.get("/api/power/mode")
async def power_mode(request: Request) -> Dict[str, Any]:
    """Return the current power mode, its uptime and the available modes."""
    power = _brain(request).power
    return {
        "mode": power.current_mode(),
        "uptime_seconds": power.mode_uptime(),
        "modes": list(power.modes),
    }


@router.post("/api/power/set-mode")
async def set_power_mode(request: Request, mode: str) -> Dict[str, Any]:
    """Set the power mode.

    Raises:
        HTTPException: 400 for an unknown mode; 409 when a low battery overrules
            a request for performance mode. The battery is the user's hardware
            and the answer is not ours to give.
    """
    power = _brain(request).power
    try:
        applied = power.set_mode(mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "unknown_mode", "reason": str(exc)}
        ) from exc
    if not applied:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "overruled_by_battery",
                "reason": "performance mode refused: the battery is below "
                f"{power.low_battery_percent}% and the machine is not on AC",
                "mode": power.current_mode(),
            },
        )
    return {"status": "ok", "mode": power.current_mode()}


@router.get("/api/thermal/status")
async def thermal_status(request: Request) -> Dict[str, Any]:
    """Return the thermal level, the thresholds and which sensors are working."""
    brain = _brain(request)
    return {
        "status": brain.thermal.check(),
        "thresholds": {
            "gpu": {
                "warn": brain.thermal.gpu_warn,
                "throttle": brain.thermal.gpu_throttle,
                "critical": brain.thermal.gpu_critical,
            },
            "cpu": {
                "warn": brain.thermal.cpu_warn,
                "throttle": brain.thermal.cpu_throttle,
                "critical": brain.thermal.cpu_critical,
            },
        },
        "cloud_only": brain.models.cloud_only,
    }


@router.get("/api/thermal/history")
async def thermal_history(request: Request, count: int = 60) -> Dict[str, Any]:
    """Return recent temperature readings for the chart.

    Args:
        request: Used to reach the Hypothalamus.
        count: How many readings to return, clamped to 1-3600.
    """
    readings = _brain(request).thermal.history(count=max(1, min(3600, count)))
    return {"history": readings, "count": len(readings), "retention_seconds": 3600}
