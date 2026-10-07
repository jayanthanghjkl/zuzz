"""Temporal Lobe - Main orchestrator for speech-to-text."""

import asyncio
import logging
import numpy as np
from typing import Optional, Callable, AsyncGenerator
from dataclasses import dataclass

from .whisper_engine import WhisperEngine
from .audio_capture import AudioCapture, CaptureConfig
from .streaming_transcription import StreamingTranscription, TranscriptionSegment
from .noise_detection import NoiseDetector
from .exceptions import TemporalLobeError

logger = logging.getLogger(__name__)


@dataclass
class ListeningSession:
    """Represents an active listening session."""
    session_id: str
    config: CaptureConfig
    is_active: bool = True
    partial_results: list = None

    def __post_init__(self):
        if self.partial_results is None:
            self.partial_results = []


class TemporalLobe:
    """Orchestrator for speech-to-text processing."""

    def __init__(
        self,
        whisper_model: str = "tiny",
        device: Optional[str] = None,
        language: Optional[str] = None,
    ):
        self.whisper = WhisperEngine(
            model_name=whisper_model,
            device=device,
            language=language,
        )
        self.noise_detector = NoiseDetector()
        self._sessions: dict = {}
        self._session_counter = 0

    async def start_listening(
        self,
        decision: dict,
        config: Optional[CaptureConfig] = None,
        callback: Optional[Callable[[TranscriptionSegment], None]] = None,
    ) -> str:
        """Start a new listening session."""
        session_id = f"listen_{self._session_counter}"
        self._session_counter += 1

        session_config = config or CaptureConfig()
        session = ListeningSession(session_id=session_id, config=session_config)
        self._sessions[session_id] = session

        streaming = StreamingTranscription(
            whisper_engine=self.whisper,
            config=session_config,
        )
        if callback:
            streaming.set_callback(callback)

        session.streaming = streaming
        await streaming.start()
        logger.info(f"Started listening session: {session_id}")
        return session_id

    async def stop_listening(self, decision: dict, session_id: str) -> Optional[str]:
        """Stop a listening session and return final transcription."""
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"Session not found: {session_id}")
            return None

        if hasattr(session, 'streaming'):
            await session.streaming.stop()

        final_text = " ".join(session.partial_results) if session.partial_results else ""
        del self._sessions[session_id]
        logger.info(f"Stopped listening session: {session_id}")
        return final_text

    async def transcribe_file(self, decision: dict, file_path: str) -> str:
        """Transcribe an audio file."""
        try:
            text = await self.whisper.transcribe_file_async(file_path)
            logger.info(f"Transcribed file: {file_path}")
            return text
        except Exception as e:
            logger.error(f"File transcription failed: {e}")
            raise TemporalLobeError(f"Transcription failed: {e}")

    async def stream_transcription(
        self,
        decision: dict,
        callback: Optional[Callable[[TranscriptionSegment], None]] = None,
    ) -> AsyncGenerator[TranscriptionSegment, None]:
        """Stream transcription results in real-time."""
        session_id = await self.start_listening(decision, callback=callback)
        session = self._sessions[session_id]

        try:
            async for segment in session.streaming.stream():
                session.partial_results.append(segment.text)
                yield segment
        finally:
            await self.stop_listening(decision, session_id)

    def get_session(self, session_id: str) -> Optional[ListeningSession]:
        """Get session by ID."""
        return self._sessions.get(session_id)

    def get_active_sessions(self) -> list:
        """Get all active session IDs."""
        return list(self._sessions.keys())

    def is_listening(self, session_id: str) -> bool:
        """Check if session is active."""
        session = self._sessions.get(session_id)
        return session is not None and session.is_active

    def set_noise_profile(self, noise_audio: np.ndarray) -> None:
        """Set noise profile for filtering."""
        self.noise_detector.learn_noise_profile(noise_audio)

    def filter_audio(self, audio: np.ndarray) -> np.ndarray:
        """Filter noise from audio."""
        return self.noise_detector.filter_audio(audio)
