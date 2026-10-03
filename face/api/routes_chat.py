"""Face API — chat routes.

HTTP and WebSocket endpoints for talking to the brain. Every request goes
through the Thalamus; nothing here calls a model directly.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from brain.cerebrum.cerebrum import CerebrumError

router = APIRouter()

_INTERNET_CACHE: Dict[str, Any] = {"value": True, "checked_at": 0.0}
INTERNET_TTL = 30.0


class ChatRequest(BaseModel):
    """Body of a chat message."""

    message: str = Field(..., min_length=1, max_length=8000)
    level: Optional[int] = Field(
        default=None, ge=1, le=5, description="Force a routing level instead of auto"
    )


async def internet_available() -> bool:
    """Check outbound connectivity, cached briefly to avoid per-message cost.

    Returns:
        True when a cloud provider is likely reachable.
    """
    if time.time() - _INTERNET_CACHE["checked_at"] < INTERNET_TTL:
        return bool(_INTERNET_CACHE["value"])
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.get("https://api.groq.com", timeout=3.0)
        value = True
    except Exception:
        value = False
    _INTERNET_CACHE.update(value=value, checked_at=time.time())
    return value


@router.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> Dict[str, Any]:
    """Answer a chat message.

    Args:
        payload: The user message and an optional forced level.
        request: Used to reach the shared Thalamus instance.

    Returns:
        The answer plus routing metadata.

    Raises:
        HTTPException: 400 for an empty message, 503 when all backends fail.
    """
    thalamus = request.app.state.thalamus
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        if payload.level is not None:
            result = await thalamus.cerebrum.think(message, payload.level)
            result.update({"reason": "forced level", "degraded": False})
        else:
            result = await thalamus.route(message, await internet_available())
    except CerebrumError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@router.post("/api/classify")
async def classify(payload: ChatRequest, request: Request) -> Dict[str, Any]:
    """Classify a message without sending it to any model.

    Args:
        payload: The message to classify.
        request: Used to reach the shared Thalamus instance.

    Returns:
        The routing decision.
    """
    thalamus = request.app.state.thalamus
    return thalamus.classify(payload.message, await internet_available())


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stream a chat reply over a WebSocket.

    Expects ``{"message": str, "level": int | None}`` and emits token events
    followed by a single done event.

    Args:
        websocket: The accepted connection.
    """
    await websocket.accept()
    thalamus = websocket.app.state.thalamus
    try:
        while True:
            data = await websocket.receive_json()
            message = str(data.get("message", "")).strip()
            if not message:
                await websocket.send_json({"type": "error", "content": "Empty message"})
                continue
            try:
                async for event in thalamus.route_stream(message, await internet_available()):
                    await websocket.send_json(event)
            except CerebrumError as exc:
                await websocket.send_json({"type": "error", "content": str(exc)})
    except WebSocketDisconnect:
        return
