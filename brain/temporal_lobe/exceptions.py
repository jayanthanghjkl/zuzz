"""Exceptions for temporal lobe module."""


class TemporalLobeError(Exception):
    """Base exception for temporal lobe module."""
    pass


class WhisperError(TemporalLobeError):
    """Whisper engine error."""
    pass


class AudioCaptureError(TemporalLobeError):
    """Audio capture error."""
    pass


class StreamingError(TemporalLobeError):
    """Streaming transcription error."""
    pass


class NoiseDetectionError(TemporalLobeError):
    """Noise detection error."""
    pass
