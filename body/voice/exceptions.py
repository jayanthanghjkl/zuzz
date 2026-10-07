"""Exceptions for voice module."""


class VoiceError(Exception):
    """Base exception for voice module."""
    pass


class SpeakerError(VoiceError):
    """Speaker hardware error."""
    pass


class AudioMixerError(VoiceError):
    """Audio mixer error."""
    pass


class LiveKitError(VoiceError):
    """LiveKit bridge error."""
    pass