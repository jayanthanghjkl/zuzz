"""Real, awaited voice tools. Unavailable services fail instead of faking success."""

import asyncio
import json
from pathlib import Path
from typing import Any

from body.fingers.guards import guard_read, resolve_target
from .tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget

# name -> (context service, permission category, description)
_OPERATIONS = {
    "voice_start_listening": ("temporal_lobe", "voice_input", "Start server microphone transcription"),
    "voice_stop_listening": ("temporal_lobe", "voice_input", "Stop server microphone transcription"),
    "voice_transcribe_file": ("temporal_lobe", "voice_input", "Transcribe an audio file with Whisper"),
    "voice_speak": ("broca_area", "voice_output", "Synthesize and play speech on the server"),
    "voice_stop_speaking": ("broca_area", "voice_output", "Interrupt speech playback"),
    "voice_set_voice": ("broca_area", "voice_output", "Select the TTS voice"),
    "voice_set_emotion": ("broca_area", "voice_output", "Select speech tone"),
    "voice_enable_wake_word": ("reticular_formation", "wake_word_control", "Enable a configured wake detector"),
    "voice_disable_wake_word": ("reticular_formation", "wake_word_control", "Disable the wake detector"),
    "voice_get_status": (None, "voice_input", "Read actual voice subsystem status"),
}


def _service(context: ToolContext, name: str) -> Any:
    """Require the operation's service to be attached."""
    attribute = _OPERATIONS[name][0]
    service = getattr(context, attribute, None) if attribute else context
    if service is None:
        raise ToolRefused(f"{name}: {attribute} unavailable; check voice status and dependencies")
    return service


def _resolve(name: str, params: dict, context: ToolContext) -> ToolTarget:
    """Bind the decision to the exact submitted voice operation and parameters."""
    _service(context, name)
    if name == "voice_transcribe_file":
        guard_read(resolve_target(params["file_path"]))
    return ToolTarget(category=_OPERATIONS[name][1],
                      target=f"{name}:{json.dumps(params, sort_keys=True)}",
                      preview={"description": _OPERATIONS[name][2]})


async def _execute(name: str, decision: Any, params: dict, context: ToolContext) -> dict:
    """Await actual work and only return success after the operation succeeds."""
    service = _service(context, name)
    if name == "voice_start_listening":
        from brain.temporal_lobe.audio_capture import CaptureConfig
        vad = params.get("vad_enabled", True)
        config = CaptureConfig(language=params.get("language"), vad_enabled=vad in (True, "true"))
        session = await service.start_listening(decision, config)
        return {"status": "listening", "session_id": session}
    if name == "voice_stop_listening":
        text = await service.stop_listening(decision, params["session_id"])
        return {"status": "stopped", "text": text}
    if name == "voice_transcribe_file":
        path = resolve_target(params["file_path"])
        guard_read(path)
        return {"text": await service.transcribe_file(decision, path)}
    if name == "voice_speak":
        path = await service.speak(decision, params["text"], params.get("emotion", "neutral"), params.get("voice_id"))
        try:
            await service.play_audio(decision, path)
            return {"status": "spoken", "text_length": len(params["text"])}
        finally:
            Path(path).unlink(missing_ok=True)
    if name == "voice_stop_speaking":
        await service.stop_speaking(decision)
        return {"status": "stopped"}
    if name == "voice_set_voice":
        if not await asyncio.to_thread(service.set_voice, params["voice_id"]):
            raise ToolRefused("Unsupported voice")
        return {"status": "voice_set", "voice_id": params["voice_id"]}
    if name == "voice_set_emotion":
        service.set_emotion(params["emotion"])
        return {"status": "emotion_set", "emotion": params["emotion"]}
    if name == "voice_enable_wake_word":
        if service.wake_word is None:
            raise ToolRefused("Configure Porcupine and a keyword model first; use push-to-talk meanwhile")
        await service.start_wake_word_detection(decision)
        if not service.wake_word.is_listening():
            raise ToolRefused("Wake detector did not start")
        return {"status": "wake_word_enabled"}
    if name == "voice_disable_wake_word":
        await service.stop_wake_word_detection(decision)
        return {"status": "wake_word_disabled"}
    return {
        "listening": context.temporal_lobe.is_listening() if context.temporal_lobe else False,
        "speaking": context.broca_area.is_speaking() if context.broca_area else False,
        "wake_word_active": bool(context.reticular_formation and context.reticular_formation.wake_word
                                 and context.reticular_formation.wake_word.is_listening()),
    }


def _tool(name: str) -> ToolSpec:
    """Build a tool without losing async execution or the schema's stable names."""
    def resolve(params: dict, context: ToolContext) -> ToolTarget:
        """Resolve before the common action gate."""
        return _resolve(name, params, context)

    async def execute(decision: Any, params: dict, context: ToolContext) -> dict:
        """Execute through the actual component, not a queued unobserved task."""
        return await _execute(name, decision, params, context)

    return ToolSpec(name=name, summary=_OPERATIONS[name][2], resolve=resolve, execute=execute,
                    returns="Actual operation result, or an explicit unavailable/error outcome")


VOICE_TOOLS = tuple(_tool(name) for name in (
    "voice_start_listening", "voice_stop_listening", "voice_speak", "voice_stop_speaking",
    "voice_transcribe_file", "voice_set_voice", "voice_set_emotion", "voice_enable_wake_word",
    "voice_disable_wake_word", "voice_get_status",
))
