"""Face API — communication routes.

Endpoints:
- POST /api/comm/send (auto channel)
- POST /api/comm/telegram/send
- POST /api/comm/email/send
- POST /api/comm/push/send
- POST /api/comm/escalate
- GET /api/comm/channels/status
- POST /api/comm/preferences
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


def _hub(request: Request) -> Any:
    hub = getattr(request.app.state, "communication_hub", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="communication hub not attached")
    return hub


class TelegramSendRequest(BaseModel):
    message: str = Field(..., description="Message text")
    buttons: Optional[list] = Field(None, description="Button labels")
    chat_id: Optional[str] = Field(None, description="Target chat ID")


class EmailSendRequest(BaseModel):
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body")
    to: Optional[str] = Field(None, description="Recipient email")


class PushSendRequest(BaseModel):
    title: str = Field(..., description="Notification title")
    message: str = Field(..., description="Notification message")
    priority: str = Field("normal", description="Priority: low, normal, high, urgent")


class EscalateRequest(BaseModel):
    message: str = Field(..., description="Message to escalate")
    start_channel: str = Field("web", description="Starting channel")


class PreferencesRequest(BaseModel):
    quiet_hours_start: Optional[str] = Field(None, description="Quiet hours start, e.g. 22:00")
    quiet_hours_end: Optional[str] = Field(None, description="Quiet hours end, e.g. 07:00")
    priority_levels: Optional[Dict[str, str]] = Field(None, description="Priority overrides")


@router.post("/api/comm/send")
async def auto_send(request: Request, body: TelegramSendRequest) -> Dict[str, Any]:
    """Send a notification via the best available channel."""
    hub = _hub(request)
    try:
        return await hub.send_notification(body.message, channel="auto", buttons=body.buttons)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/comm/telegram/send")
async def telegram_send(request: Request, body: TelegramSendRequest) -> Dict[str, Any]:
    """Send a Telegram message."""
    hub = _hub(request)
    try:
        return await hub.send_telegram(body.message, buttons=body.buttons, chat_id=body.chat_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/comm/email/send")
async def email_send(request: Request, body: EmailSendRequest) -> Dict[str, Any]:
    """Send an email."""
    hub = _hub(request)
    try:
        return await hub.send_email(body.subject, body.body, to=body.to)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/comm/push/send")
async def push_send(request: Request, body: PushSendRequest) -> Dict[str, Any]:
    """Send a push notification."""
    hub = _hub(request)
    try:
        return await hub.send_push(body.title, body.message, priority=body.priority)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/comm/escalate")
async def escalate(request: Request, body: EscalateRequest) -> Dict[str, Any]:
    """Escalate a message through channels."""
    hub = _hub(request)
    try:
        return await hub.escalate(body.message, start_channel=body.start_channel)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/comm/channels/status")
async def channels_status(request: Request) -> Dict[str, Any]:
    """Get channel status."""
    hub = _hub(request)
    return hub.get_channel_status()


@router.post("/api/comm/preferences")
async def set_preferences(request: Request, body: PreferencesRequest) -> Dict[str, Any]:
    """Set communication preferences."""
    hub = _hub(request)
    prefs = {
        "quiet_hours_start": body.quiet_hours_start,
        "quiet_hours_end": body.quiet_hours_end,
        "priority_levels": body.priority_levels or {},
    }
    return {"status": "saved", "preferences": prefs}
