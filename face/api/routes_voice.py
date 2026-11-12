"""Voice controls use app-scoped services and never advertise stub success."""

import asyncio
import importlib.util
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .voice_support import authorize, component

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


class ListenStartRequest(BaseModel):
    """Server-microphone request, separate from browser recording."""
    language: Optional[str] = None
    vad_enabled: bool = True
    confirmation_id: str = ""


class SpeakRequest(BaseModel):
    """Bounded speech request; output is playable audio, not a local path."""
    text: str = Field(min_length=1, max_length=8000)
    emotion: str = "neutral"
    voice_id: Optional[str] = None


class SetVoiceRequest(BaseModel):
    """An engine voice selection."""
    voice_id: str


class WakeWordRequest(BaseModel):
    """Optional configured server wake detector request."""
    keywords: Optional[list[str]] = None
    confirmation_id: str = ""


@router.get("/status")
async def get_status(request: Request) -> dict:
    """Report installed/configured services without downloading models."""
    tl = getattr(request.app.state, "temporal_lobe", None)
    ba = getattr(request.app.state, "broca_area", None)
    rf = getattr(request.app.state, "reticular_formation", None)
    return {
        "listening": {"active_sessions": tl.get_active_sessions()} if tl else False,
        "speaking": ba.get_status() if ba else False,
        "wake_word": rf.get_status() if rf else False,
        "tts_engine": ba.tts.engine if ba else "unavailable",
        "whisper_model": "configured (loaded on first use)" if tl and importlib.util.find_spec("whisper") else "unavailable: install requirements-voice.txt",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "desktop_error": getattr(request.app.state, "desktop_error", ""),
        "browser_audio": "Browser recognition/synthesis depend on browser support; recording upload requires Whisper and ffmpeg",
    }


@router.post("/listen/start")
async def start_listening(payload: ListenStartRequest, request: Request) -> dict:
    """Start real server capture only after a gate decision and any approval."""
    tl = component(request, "temporal_lobe")
    decision = authorize(request, "voice_input", f"listen:{payload.language}:{payload.vad_enabled}", payload.confirmation_id)
    if isinstance(decision, JSONResponse):
        return decision
    from brain.temporal_lobe.audio_capture import CaptureConfig
    try:
        session = await tl.start_listening(decision, CaptureConfig(language=payload.language, vad_enabled=payload.vad_enabled))
        return {"session_id": session, "status": "listening"}
    except Exception as exc:
        raise HTTPException(503, f"Could not start microphone: {exc}") from exc


@router.post("/listen/stop")
async def stop_listening(session_id: str, request: Request) -> dict:
    """Stop is always permitted so an unavailable gate cannot trap recording."""
    tl = component(request, "temporal_lobe")
    text = await tl.stop_listening(None, session_id)
    return {"session_id": session_id, "text": text, "status": "stopped"}


@router.post("/speak")
async def speak(payload: SpeakRequest, request: Request) -> FileResponse:
    """Synthesize and deliver actual audio bytes, deleting them after delivery."""
    ba = component(request, "broca_area")
    decision = authorize(request, "voice_output", "synthesize speech")
    if isinstance(decision, JSONResponse):
        return decision
    try:
        path = await ba.speak(decision, payload.text, payload.emotion, payload.voice_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Speech generation failed")
        raise HTTPException(503, f"Speech generation unavailable: {exc}") from exc
    if request.app.state.kill_switch.is_killed():
        Path(path).unlink(missing_ok=True)
        raise HTTPException(409, "Kill switch engaged during speech generation")
    media_type = "audio/mpeg" if str(path).endswith(".mp3") else "audio/wav"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(Path(path).unlink, missing_ok=True))


@router.post("/interrupt")
async def interrupt_speaking(request: Request) -> dict:
    """Cancel synthesis; the browser also stops its playback immediately."""
    ba = component(request, "broca_area")
    await ba.stop_speaking(None)
    return {"status": "interrupted"}


@router.get("/available-voices")
async def get_available_voices(request: Request) -> dict:
    """List only the configured backend, without remote discovery at startup."""
    ba = getattr(request.app.state, "broca_area", None)
    return {"voices": await asyncio.to_thread(ba.get_available_voices) if ba else []}


@router.post("/set-voice")
async def set_voice(payload: SetVoiceRequest, request: Request) -> dict:
    """Change the configured backend voice after authorization."""
    ba = component(request, "broca_area")
    decision = authorize(request, "voice_output", "set voice")
    if isinstance(decision, JSONResponse):
        return decision
    success = await asyncio.to_thread(ba.set_voice, payload.voice_id)
    if not success:
        raise HTTPException(400, "Unsupported voice")
    return {"voice_id": payload.voice_id, "success": True}


@router.post("/wake-word/{operation}")
async def wake_word(operation: str, payload: WakeWordRequest, request: Request) -> dict:
    """Refuse missing hardware/model configuration instead of claiming enabled."""
    if operation not in ("enable", "disable"):
        raise HTTPException(404, "Unknown wake-word operation")
    rf = component(request, "reticular_formation")
    if operation == "disable":
        await rf.stop_wake_word_detection(None)
        return {"status": "wake_word_disabled"}
    if rf.wake_word is None:
        raise HTTPException(503, "Wake word requires a configured Porcupine access key and keyword model; use push-to-talk")
    decision = authorize(request, "wake_word_control", "enable wake word", payload.confirmation_id)
    if isinstance(decision, JSONResponse):
        return decision
    await rf.start_wake_word_detection(decision)
    if not rf.wake_word.is_listening():
        raise HTTPException(503, "Wake detector did not start")
    return {"status": "wake_word_enabled"}


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket) -> None:
    """Explicitly retire the acknowledgement-only protocol; use recording upload."""
    await websocket.accept()
    await websocket.send_json({"type": "error", "content": "Use POST /api/voice/transcribe with a complete recording; live audio streaming is not implemented"})
    await websocket.close(code=1003)


def set_dependencies(tl, ba, rf) -> None:
    """Compatibility hook; dependencies now live on request.app.state only."""
