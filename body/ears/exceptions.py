"""Exceptions for ears module."""


class EarsError(Exception):
    """Base exception for ears module."""
    pass


class MicrophoneError(EarsError):
    """Microphone hardware error."""
    pass


class AudioStreamError(EarsError):
    """Audio stream error."""
    pass


class VADError(EarsError):
    """Voice Activity Detection error."""
    pass