"""Ears module - Microphone input and audio capture."""

from .microphone import Microphone
from .audio_stream import AudioStream
from .vad import VoiceActivityDetector
from .exceptions import EarsError, MicrophoneError, AudioStreamError, VADError

__all__ = [
    "Microphone",
    "AudioStream",
    "VoiceActivityDetector",
    "EarsError",
    "MicrophoneError",
    "AudioStreamError",
    "VADError",
]