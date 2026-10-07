"""Voice API routes for hands-free interaction."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from typing: Optional, List, Dict, Any
import logging
import asyncio
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Global references (will be set by server.py)
temporal_lobe = None
broca_area = None
reticular_formation = None


class ListenStartRequest(BaseModel):
    language: Optional[str] = None
    vad_enabled: bool = True


class SpeakRequest(BaseModel):
    text: str
    emotion: str = "neutral"
    voice_id: Optional[str] = None


class SetVoiceRequest(BaseModel):
    voice_id: str


class SetEmotionRequest(BaseModel):
    emotion: str


class WakeWordRequest(BaseModel):
    keywords: Optional[List[str]] = None


@router.post("/listen/start")
async def start_listening(request: ListenStartRequest) -> Dict[str, Any]:
    """Start voice input mode."""
    try:
        if not temporal_lobe:
            raise HTTPException(status_code=503, detail="Temporal lobe not initialized")

        from brain.temporal_lobe.audio_capture import CaptureConfig
        config = CaptureConfig(
            language=request.language,
            vad_enabled=request.vad_enabled,
        )

        session_id = await temporal_lobe.start_listening(
            decision={},
            config=config,
        )

        return {"session_id": session_id, "status": "listening"}
    except Exception as e:
        logger.error(f"Start listening failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/listen/stop")
async def stop_listening(session_id: str) -> Dict[str, Any]:
    """Stop voice input mode."""
    try:
        if not temporal_lobe:
            raise HTTPException(status_code=503, detail="Temporal lobe not initialized")

        text = await temporal_lobe.stop_listening(decision={}, session_id=session_id)
        return {"session_id": session_id, "text": text, "status": "stopped"}
    except Exception as e:
        logger.error(f"Stop listening failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speak")
async def speak(request: SpeakRequest) -> Dict[str, Any]:
    """Convert text to speech."""
    try:
        if not broca_area:
            raise HTTPException(status_code=503, detail="Broca's area not initialized")

        from brain.broca_area.emotion_tone import Emotion
        emotion = Emotion(request.emotion)

        audio_file = await broca_area.speak(
            decision={},
            text=request.text,
            emotion=emotion,
            voice_id=request.voice_id,
        )

        return {"audio_file": audio_file, "status": "spoken"}
    except Exception as e:
        logger.error(f"Speak failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/interrupt")
async def interrupt_speaking() -> Dict[str, Any]:
    """Interrupt current speech."""
    try:
        if not broca_area:
            raise HTTPException(status_code=503, detail="Broca's area not initialized")

        await broca_area.stop_speaking(decision={})
        return {"status": "interrupted"}
    except Exception as e:
        logger.error(f"Interrupt failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """Get voice system status."""
    try:
        status = {}

        if temporal_lobe:
            status["listening"] = {
                "active_sessions": temporal_lobe.get_active_sessions(),
            }

        if broca_area:
            status["speaking"] = broca_area.get_status()

        if reticular_formation:
            status["wake_word"] = reticular_formation.get_status()

        return status
    except Exception as e:
        logger.error(f"Get status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wake-word/enable")
async def enable_wake_word(request: WakeWordRequest) -> Dict[str, Any]:
    """Enable wake word detection."""
    try:
        if not reticular_formation:
            raise HTTPException(status_code=503, detail="Reticular formation not initialized")

        await reticular_formation.start_wake_word_detection(decision={})
        return {"status": "wake_word_enabled"}
    except Exception as e:
        logger.error(f"Enable wake word failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wake-word/disable")
async def disable_wake_word() -> Dict[str, Any]:
    """Disable wake word detection."""
    try:
        if not reticular_formation:
            raise HTTPException(status_code=503, detail="Reticular formation not initialized")

        await reticular_formation.stop_wake_word_detection(decision={})
        return {"status": "wake_word_disabled"}
    except Exception as e:
        logger.error(f"Disable wake word failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/available-voices")
async def get_available_voices() -> Dict[str, Any]:
    """Get available TTS voices."""
    try:
        if not broca_area:
            raise HTTPException(status_code=503, detail="Broca's area not initialized")

        voices = broca_area.get_available_voices()
        return {"voices": voices}
    except Exception as e:
        logger.error(f"Get voices failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/set-voice")
async def set_voice(request: SetVoiceRequest) -> Dict[str, Any]:
    """Set active TTS voice."""
    try:
        if not broca_area:
            raise HTTPException(status_code=503, detail="Broca's area not initialized")

        success = broca_area.set_voice(request.voice_id)
        return {"voice_id": request.voice_id, "success": success}
    except Exception as e:
        logger.error(f"Set voice failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# WebSocket for real-time audio streaming
@router.websocket("/stream")
async def voice_stream(websocket: WebSocket):
    """WebSocket for real-time voice streaming."""
    await websocket.accept()
    logger.info("Voice WebSocket connected")

    try:
        while True:
            data = await websocket.receive_bytes()
            # Process audio chunk
            # This would integrate with temporal_lobe for streaming transcription
            await websocket.send_json({"status": "received", "size": len(data)})
    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()


def set_dependencies(tl, ba, rf):
    """Set module dependencies."""
    global temporal_lobe, broca_area, reticular_formation
    temporal_lobe = tl
    broca_area = ba
    reticular_formation = rf