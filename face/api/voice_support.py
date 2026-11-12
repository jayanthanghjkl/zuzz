"""Bounded voice consent, decoding and error handling for browser audio."""

import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import replace
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.amygdala.threat_detector import ThreatDetector
from brain.voice_guards import verify_voice
from core.audit import CONFIRMATION_GRANTED, CONFIRMATION_REQUESTED, BLOCKED
from .file_confirmations import PendingConfirmations


def authorize(request: Request, category: str, target: str, confirmation_id: str = "") -> Any:
    """Re-decide each operation; require a matching, single-use human approval."""
    state = request.app.state
    gate = PermissionGate(kill_switch=state.kill_switch)
    action = Action(category=ActionCategory(category), target=target)
    decision = gate.decide(action, state.trust.current(), ThreatDetector().assess(action))
    audit = getattr(state, "audit", None)
    if decision.blocked:
        if audit:
            audit.record(BLOCKED, decision.reason, category=category, target=target)
        raise HTTPException(409 if state.kill_switch.is_killed() else 403, decision.reason)
    if not hasattr(state, "voice_confirmations"):
        state.voice_confirmations = PendingConfirmations()
    pending = state.voice_confirmations
    if confirmation_id:
        entry = pending.pop(confirmation_id)
        if (not entry or time.time() - entry["created"] > 120
                or entry["action"] != action):
            raise HTTPException(400, "Unknown, expired or mismatched voice confirmation")
        decision = replace(decision, allowed=True, requires_confirmation=False, decision="auto")
        if audit:
            audit.record(CONFIRMATION_GRANTED, "Voice operation approved", category=category, target=target)
    elif decision.requires_confirmation:
        identifier = pending.create(action, decision, {})
        if audit:
            audit.record(CONFIRMATION_REQUESTED, decision.reason, category=category, target=target)
        return JSONResponse(status_code=202, content={
            "status": "confirmation_required", "confirmation_id": identifier,
            "detail": decision.reason,
        })
    verify_voice(decision, category, state.kill_switch)
    return decision


def component(request: Request, name: str) -> Any:
    """Return an initialized service or an actionable unavailable error."""
    service = getattr(request.app.state, name, None)
    if service is None:
        raise HTTPException(503, f"{name} unavailable; see /api/voice/status and install/configure voice dependencies")
    return service


async def decode_audio(data: bytes, suffix: str) -> Any:
    """Decode at most 60 seconds to mono 16 kHz PCM using bounded ffmpeg work."""
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "Install ffmpeg and add it to PATH for browser recording transcription")
    import numpy as np
    fd, path = tempfile.mkstemp(suffix=suffix)
    process = None
    try:
        with os.fdopen(fd, "wb") as audio:
            audio.write(data)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-v", "error", "-i", path,
            "-t", "60", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            pcm, errors = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError as exc:
            raise HTTPException(408, "Audio decoding timed out") from exc
        if process.returncode or not pcm:
            raise HTTPException(400, "Invalid or empty audio recording")
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        os.unlink(path)
