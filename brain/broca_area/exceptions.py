"""Exceptions for Broca's Area module."""


class BrocaAreaError(Exception):
    """Base exception for Broca's Area module."""
    pass


class TTSError(BrocaAreaError):
    """TTS engine error."""
    pass


class VoiceSelectionError(BrocaAreaError):
    """Voice selection error."""
    pass


class EmotionError(BrocaAreaError):
    """Emotion tone error."""
    pass


class StreamingError(BrocaAreaError):
    """Streaming speech error."""
    pass
