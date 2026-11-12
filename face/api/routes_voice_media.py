"""Browser microphone upload, bounded decoding and real Whisper transcription."""

import asyncio
import hashlib
import importlib.util

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from brain.voice_guards import verify_voice
from .voice_support import authorize, component, decode_audio

router = APIRouter(prefix="/api/voice", tags=["voice"])
MAX_AUDIO_BYTES = 8 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav"}


@router.post("/transcribe")
async def transcribe(request: Request, language: str = "en", confirmation_id: str = "") -> dict:
    """Transcribe a complete raw audio body, not arbitrary user file paths.

    At low trust levels returns 202; resend the same recording with the approved
    confirmation_id. The target is bound to its content digest and language.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    if content_type not in AUDIO_TYPES:
        raise HTTPException(415, "Send audio/webm, audio/ogg, audio/mp4 or audio/wav")
    if len(language) > 12 or not language.replace("-", "").isalpha():
        raise HTTPException(400, "Invalid transcription language")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording exceeds 8 MiB; record at most 60 seconds")
    if not data:
        raise HTTPException(400, "Empty audio recording")
    tl = component(request, "temporal_lobe")
    decision = authorize(request, "voice_input", f"recording:{language}:{hashlib.sha256(data).hexdigest()}", confirmation_id)
    if isinstance(decision, JSONResponse):
        return decision
    if not importlib.util.find_spec("whisper"):
        raise HTTPException(503, "Install requirements-voice.txt for Whisper, or use browser speech recognition")
    state = request.app.state
    if not hasattr(state, "transcription_lock"):
        state.transcription_lock = asyncio.Lock()
    if state.transcription_lock.locked():
        raise HTTPException(429, "Another recording is being transcribed; try again shortly")
    async with state.transcription_lock:
        audio = await decode_audio(bytes(data), AUDIO_TYPES[content_type])
        if state.kill_switch.is_killed():
            raise HTTPException(409, "Kill switch engaged during transcription")
        verify_voice(decision, "voice_input", state.kill_switch)
        import numpy as np
        if not audio.size or float(np.max(np.abs(audio))) < 0.005:
            return {"text": "", "status": "no_speech"}
        try:
            result = await tl.whisper.transcribe_async(audio, language=language)
        except Exception as exc:
            raise HTTPException(503, f"Whisper transcription unavailable: {exc}") from exc
        if state.kill_switch.is_killed():
            raise HTTPException(409, "Kill switch engaged during transcription")
        verify_voice(decision, "voice_input", state.kill_switch)
    return {"text": str(result.get("text", "")).strip(), "status": "transcribed"}
