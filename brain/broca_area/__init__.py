"""Broca's Area - Text-to-Speech processing."""

from .broca_area import BrocaArea
from .tts_engine import TTSEngine
from .voice_selection import VoiceSelector
from .emotion_tone import EmotionTone
from .streaming_speech import StreamingSpeech
from .exceptions import BrocaAreaError, TTSError, VoiceSelectionError, EmotionError, StreamingError

__all__ = [
    "BrocaArea",
    "TTSEngine",
    "VoiceSelector",
    "EmotionTone",
    "StreamingSpeech",
    "BrocaAreaError",
    "TTSError",
    "VoiceSelectionError",
    "EmotionError",
    "StreamingError",
]