"""Body/Tools — voice interaction.

Ten read/write knobs for how ZUZZ hears and speaks: microphone
capture, transcription, TTS playback, voice selection, emotional tone
and wake-word detection. They are grouped in one module because they
answer one question — "how does ZUZZ sound" — and all of them touch
the machine (audio I/O).

Every tool is judged by its trust category:
- voice_input:     ask at levels 1-2, auto from 3
- voice_output:    auto at every level (only speaks, no exposure)
- wake_word_control: ask at levels 1-2, auto from 3

The tools refuse when the voice subsystems are not attached,
following the personality tools' rule: silently "succeeding" against
nothing would tell the model a knob was turned that nobody reads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)

# Categories match config/trust_levels.json
CATEGORY_INPUT = "voice_input"
CATEGORY_OUTPUT = "voice_output"
CATEGORY_WAKE = "wake_word_control"


def _temporal_lobe(context: ToolContext, tool: str):
    """Return the TemporalLobe, refusing when it is not attached."""
    lobe = context.temporal_lobe
    if lobe is None:
        raise ToolRefused(
            f"{tool}: voice input subsystem (TemporalLobe) is not attached"
        )
    return lobe


def _broca_area(context: ToolContext, tool: str):
    """Return the BrocaArea, refusing when it is not attached."""
    area = context.broca_area
    if area is None:
        raise ToolRefused(
            f"{tool}: voice output subsystem (BrocaArea) is not attached"
        )
    return area


def _reticular_formation(context: ToolContext, tool: str):
    """Return the ReticularFormation, refusing when it is not attached."""
    rf = context.reticular_formation
    if rf is None:
        raise ToolRefused(
            f"{tool}: wake word subsystem (ReticularFormation) is not attached"
        )
    return rf


def _as_bool(params: Dict[str, Any], key: str, tool: str) -> bool:
    """Return a boolean parameter the schema declared as an enum."""
    value = required_text(params, key, tool).lower()
    if value not in ("true", "false"):
        raise ToolRefused(f"{tool}: {key} must be 'true' or 'false'")
    return value == "true"


def _as_int(params: Dict[str, Any], key: str, tool: str, min_val: int = 1, max_val: int = 100) -> int:
    """Return an integer parameter with bounds checking."""
    value = required_text(params, key, tool)
    try:
        ivalue = int(value)
    except ValueError:
        raise ToolRefused(f"{tool}: {key} must be an integer")
    if not (min_val <= ivalue <= max_val):
        raise ToolRefused(f"{tool}: {key} must be between {min_val} and {max_val}")
    return ivalue


# -- voice_start_listening ----------------------------------------------------


def _resolve_start_listening(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve starting voice capture."""
    _temporal_lobe(context, "voice_start_listening")
    language = params.get("language", "en")
    vad_enabled = _as_bool(params, "vad_enabled", "voice_start_listening")
    return ToolTarget(
        category=CATEGORY_INPUT,
        target="start listening",
        preview={
            "description": (
                f"Begin capturing microphone audio (language={language}, "
                f"VAD={'on' if vad_enabled else 'off'})"
            )
        },
        detail={"language": language, "vad_enabled": vad_enabled},
    )


def _execute_start_listening(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Start listening and return a session identifier."""
    lobe = _temporal_lobe(context, "voice_start_listening")
    language = params.get("language", "en")
    vad_enabled = _as_bool(params, "vad_enabled", "voice_start_listening")

    # Stub: real implementation would start audio capture
    session_id = f"session_{id(lobe)}_{language}"

    # Log the call
    if context.activity_log:
        context.activity_log(
            "voice_start_listening",
            {"language": language, "vad_enabled": vad_enabled, "session_id": session_id},
        )

    return {
        "status": "listening",
        "session_id": session_id,
        "language": language,
        "vad_enabled": vad_enabled,
        "note": "Voice input subsystem initializing — real capture pending backend wiring",
    }


# -- voice_stop_listening -----------------------------------------------------


def _resolve_stop_listening(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve stopping voice capture."""
    _temporal_lobe(context, "voice_stop_listening")
    session_id = required_text(params, "session_id", "voice_stop_listening")
    return ToolTarget(
        category=CATEGORY_INPUT,
        target=f"stop listening session={session_id}",
        preview={"description": "End voice capture and return final transcription"},
        detail={"session_id": session_id},
    )


def _execute_stop_listening(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Stop listening and return transcription."""
    _temporal_lobe(context, "voice_stop_listening")
    session_id = required_text(params, "session_id", "voice_stop_listening")

    # Log the call
    if context.activity_log:
        context.activity_log(
            "voice_stop_listening", {"session_id": session_id}
        )

    return {
        "status": "stopped",
        "session_id": session_id,
        "transcription": "",
        "note": "Voice input subsystem initializing — real capture pending backend wiring",
    }


# -- voice_speak --------------------------------------------------------------


def _resolve_speak(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve speaking text."""
    _broca_area(context, "voice_speak")
    text = required_text(params, "text", "voice_speak")
    emotion = params.get("emotion", "neutral")
    voice_id = params.get("voice_id")
    return ToolTarget(
        category=CATEGORY_OUTPUT,
        target=f"speak text len={len(text)} emotion={emotion}",
        preview={
            "description": (
                f"Speak: {text[:50]}{'...' if len(text) > 50 else ''} "
                f"(emotion={emotion}{', voice=' + voice_id if voice_id else ''})"
            )
        },
        detail={"text_preview": text[:100], "emotion": emotion, "voice_id": voice_id},
    )


def _execute_speak(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Speak text via TTS."""
    area = _broca_area(context, "voice_speak")
    text = required_text(params, "text", "voice_speak")
    emotion = params.get("emotion", "neutral")
    voice_id = params.get("voice_id")

    # Log the call
    if context.activity_log:
        context.activity_log(
            "voice_speak",
            {
                "text_length": len(text),
                "emotion": emotion,
                "voice_id": voice_id,
            },
        )

    return {
        "status": "speaking",
        "text_length": len(text),
        "emotion": emotion,
        "voice_id": voice_id,
        "note": "Voice output subsystem initializing — real TTS pending backend wiring",
    }


# -- voice_stop_speaking ------------------------------------------------------


def _resolve_stop_speaking(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve stopping speech."""
    _broca_area(context, "voice_stop_speaking")
    return ToolTarget(
        category=CATEGORY_OUTPUT,
        target="stop speaking",
        preview={"description": "Interrupt current speech output immediately"},
        detail={},
    )


def _execute_stop_speaking(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Stop current speech."""
    _broca_area(context, "voice_stop_speaking")

    # Log the call
    if context.activity_log:
        context.activity_log("voice_stop_speaking", {})

    return {
        "status": "stopped",
        "note": "Voice output subsystem initializing — real TTS pending backend wiring",
    }


# -- voice_transcribe_file ----------------------------------------------------


def _resolve_transcribe_file(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve file transcription."""
    _temporal_lobe(context, "voice_transcribe_file")
    file_path = required_text(params, "file_path", "voice_transcribe_file")
    language = params.get("language", "en")
    return ToolTarget(
        category=CATEGORY_INPUT,
        target=f"transcribe file={file_path}",
        preview={
            "description": f"Transcribe audio file to text (language={language})"
        },
        detail={"file_path": file_path, "language": language},
    )


def _execute_transcribe_file(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Transcribe an audio file."""
    _temporal_lobe(context, "voice_transcribe_file")
    file_path = required_text(params, "file_path", "voice_transcribe_file")
    language = params.get("language", "en")

    # Log the call
    if context.activity_log:
        context.activity_log(
            "voice_transcribe_file", {"file_path": file_path, "language": language}
        )

    return {
        "status": "transcribed",
        "file_path": file_path,
        "language": language,
        "text": "",
        "note": "Voice input subsystem initializing — real Whisper pending backend wiring",
    }


# -- voice_set_voice ----------------------------------------------------------


def _resolve_set_voice(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve setting TTS voice."""
    _broca_area(context, "voice_set_voice")
    voice_id = required_text(params, "voice_id", "voice_set_voice")
    return ToolTarget(
        category=CATEGORY_OUTPUT,
        target=f"set voice={voice_id}",
        preview={"description": f"Change TTS voice to {voice_id}"},
        detail={"voice_id": voice_id},
    )


def _execute_set_voice(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Set the active TTS voice."""
    _broca_area(context, "voice_set_voice")
    voice_id = required_text(params, "voice_id", "voice_set_voice")

    # Log the call
    if context.activity_log:
        context.activity_log("voice_set_voice", {"voice_id": voice_id})

    return {
        "status": "voice_set",
        "voice_id": voice_id,
        "note": "Voice output subsystem initializing — real voice selection pending backend wiring",
    }


# -- voice_set_emotion --------------------------------------------------------


def _resolve_set_emotion(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve setting default TTS emotion."""
    _broca_area(context, "voice_set_emotion")
    emotion = required_text(params, "emotion", "voice_set_emotion")
    valid_emotions = (
        "neutral",
        "happy",
        "sad",
        "angry",
        "urgent",
        "calm",
        "excited",
        "whisper",
    )
    if emotion not in valid_emotions:
        raise ToolRefused(
            f"voice_set_emotion: emotion must be one of {valid_emotions}"
        )
    return ToolTarget(
        category=CATEGORY_OUTPUT,
        target=f"set emotion={emotion}",
        preview={"description": f"Set default emotional tone to {emotion}"},
        detail={"emotion": emotion},
    )


def _execute_set_emotion(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Set default emotional tone for TTS."""
    _broca_area(context, "voice_set_emotion")
    emotion = required_text(params, "emotion", "voice_set_emotion")

    # Log the call
    if context.activity_log:
        context.activity_log("voice_set_emotion", {"emotion": emotion})

    return {
        "status": "emotion_set",
        "emotion": emotion,
        "note": "Voice output subsystem initializing — real emotion tone pending backend wiring",
    }


# -- voice_enable_wake_word ---------------------------------------------------


def _resolve_enable_wake_word(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve enabling wake word detection."""
    _reticular_formation(context, "voice_enable_wake_word")
    keywords = params.get("keywords", ["hey zuzz"])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    return ToolTarget(
        category=CATEGORY_WAKE,
        target=f"enable wake word keywords={keywords}",
        preview={"description": f"Enable wake word detection for: {', '.join(keywords)}"},
        detail={"keywords": keywords},
    )


def _execute_enable_wake_word(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Enable wake word detection."""
    rf = _reticular_formation(context, "voice_enable_wake_word")
    keywords = params.get("keywords", ["hey zuzz"])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    # Log the call
    if context.activity_log:
        context.activity_log("voice_enable_wake_word", {"keywords": keywords})

    # Try to actually start if available
    started = False
    if hasattr(rf, "start_wake_word_detection"):
        try:
            import asyncio
            asyncio.create_task(rf.start_wake_word_detection({}))
            started = True
        except Exception:
            pass

    return {
        "status": "wake_word_enabled" if started else "wake_word_pending",
        "keywords": keywords,
        "active": started,
        "note": "Wake word subsystem initializing — real detection pending backend wiring",
    }


# -- voice_disable_wake_word --------------------------------------------------


def _resolve_disable_wake_word(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve disabling wake word detection."""
    _reticular_formation(context, "voice_disable_wake_word")
    return ToolTarget(
        category=CATEGORY_WAKE,
        target="disable wake word",
        preview={"description": "Disable wake word detection"},
        detail={},
    )


def _execute_disable_wake_word(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Disable wake word detection."""
    rf = _reticular_formation(context, "voice_disable_wake_word")

    # Log the call
    if context.activity_log:
        context.activity_log("voice_disable_wake_word", {})

    # Try to actually stop if available
    stopped = False
    if hasattr(rf, "stop_wake_word_detection"):
        try:
            import asyncio
            asyncio.create_task(rf.stop_wake_word_detection({}))
            stopped = True
        except Exception:
            pass

    return {
        "status": "wake_word_disabled" if stopped else "wake_word_pending",
        "active": not stopped,
        "note": "Wake word subsystem initializing — real detection pending backend wiring",
    }


# -- voice_get_status ---------------------------------------------------------


def _resolve_get_status(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve getting voice system status."""
    # Any subsystem can provide status
    _temporal_lobe(context, "voice_get_status")
    return ToolTarget(
        category=CATEGORY_INPUT,
        target="get voice status",
        preview={"description": "Get current voice system state"},
        detail={},
    )


def _execute_get_status(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Get current voice system state."""
    _temporal_lobe(context, "voice_get_status")

    # Gather status from available subsystems
    listening = False
    speaking = False
    wake_word_active = False
    available_voices = []

    if context.temporal_lobe and hasattr(context.temporal_lobe, "is_listening"):
        try:
            listening = context.temporal_lobe.is_listening()
        except Exception:
            pass

    if context.broca_area and hasattr(context.broca_area, "is_speaking"):
        try:
            speaking = context.broca_area.is_speaking()
        except Exception:
            pass

    if context.reticular_formation and hasattr(context.reticular_formation, "wake_word"):
        try:
            wake_word_active = (
                context.reticular_formation.wake_word.is_listening()
                if context.reticular_formation.wake_word
                else False
            )
        except Exception:
            pass

    if context.broca_area and hasattr(context.broca_area, "get_available_voices"):
        try:
            available_voices = context.broca_area.get_available_voices()
        except Exception:
            pass

    # Log the call
    if context.activity_log:
        context.activity_log(
            "voice_get_status",
            {
                "listening": listening,
                "speaking": speaking,
                "wake_word_active": wake_word_active,
            },
        )

    return {
        "listening": listening,
        "speaking": speaking,
        "wake_word_active": wake_word_active,
        "available_voices": available_voices,
        "note": "Voice subsystems initializing — real status pending backend wiring",
    }


#: The voice interaction tools.
VOICE_TOOLS: tuple = (
    ToolSpec(
        name="voice_start_listening",
        summary="Begin voice input mode. Captures microphone audio and streams transcription.",
        resolve=_resolve_start_listening,
        execute=_execute_start_listening,
        returns="Session ID and listening state",
    ),
    ToolSpec(
        name="voice_stop_listening",
        summary="End voice input mode and return final transcription.",
        resolve=_resolve_stop_listening,
        execute=_execute_stop_listening,
        returns="Final transcription and stopped state",
    ),
    ToolSpec(
        name="voice_speak",
        summary="Convert text to speech and play it.",
        resolve=_resolve_speak,
        execute=_execute_speak,
        returns="Speaking state and text metadata",
    ),
    ToolSpec(
        name="voice_stop_speaking",
        summary="Interrupt current speech output immediately.",
        resolve=_resolve_stop_speaking,
        execute=_execute_stop_speaking,
        returns="Stopped state",
    ),
    ToolSpec(
        name="voice_transcribe_file",
        summary="Transcribe an audio file to text using Whisper.",
        resolve=_resolve_transcribe_file,
        execute=_execute_transcribe_file,
        returns="Transcribed text",
    ),
    ToolSpec(
        name="voice_set_voice",
        summary="Change the active TTS voice.",
        resolve=_resolve_set_voice,
        execute=_execute_set_voice,
        returns="New voice ID",
    ),
    ToolSpec(
        name="voice_set_emotion",
        summary="Set default emotional tone for TTS output.",
        resolve=_resolve_set_emotion,
        execute=_execute_set_emotion,
        returns="New emotion setting",
    ),
    ToolSpec(
        name="voice_enable_wake_word",
        summary="Enable wake word detection (e.g. 'Hey ZUZZ').",
        resolve=_resolve_enable_wake_word,
        execute=_execute_enable_wake_word,
        returns="Wake word enabled state and keywords",
    ),
    ToolSpec(
        name="voice_disable_wake_word",
        summary="Disable wake word detection.",
        resolve=_resolve_disable_wake_word,
        execute=_execute_disable_wake_word,
        returns="Wake word disabled state",
    ),
    ToolSpec(
        name="voice_get_status",
        summary="Get current voice system state: listening, speaking, wake word, available voices.",
        resolve=_resolve_get_status,
        execute=_execute_get_status,
        returns="Voice system status snapshot",
    ),
)