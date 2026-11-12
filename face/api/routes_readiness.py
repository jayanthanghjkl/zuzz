"""Operational readiness is distinct from a web server merely being alive."""

import importlib.util
import shutil

from fastapi import APIRouter, Request
from brain.cerebrum.reasoning import list_local_models
from core.config import model_for_level

router = APIRouter()


@router.get("/api/health/readiness")
async def readiness(request: Request) -> dict:
    """Report verifiable configuration; never assert untested model inference."""
    state = request.app.state
    models = await list_local_models()
    expected = [model_for_level(1), model_for_level(2)]
    providers = state.thalamus.cerebrum.bridge.configured_providers()
    ba = getattr(state, "broca_area", None)
    notes = []
    if not any(model in models for model in expected):
        notes.append("No configured local model found. Start Ollama and pull LOCAL_MODEL_FAST/LOCAL_MODEL_SMART, or configure a cloud provider.")
    if not models and not providers:
        notes.append("No local model detected and no cloud provider configured: chat cannot answer yet.")
    if not importlib.util.find_spec("whisper") or not shutil.which("ffmpeg"):
        notes.append("Server recording transcription needs requirements-voice.txt and ffmpeg. Browser speech recognition is a separate, browser-dependent option.")
    return {
        "stopped": state.kill_switch.is_killed(),
        "local_models_detected": models,
        "configured_local_models": expected,
        "cloud_providers_configured": providers,
        "model_inference_verified": False,
        "desktop_available": not bool(getattr(state, "desktop_error", "")),
        "desktop_error": getattr(state, "desktop_error", ""),
        "tts_engine_configured": ba.tts.engine if ba else None,
        "notes": notes,
    }
