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
from brain.voice_guards import verify_voice

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
        kill_switch=None,
    ):
        self.kill_switch = kill_switch
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
        verify_voice(decision, "voice_input", self.kill_switch)
        session_id = f"listen_{self._session_counter}"
        self._session_counter += 1

        session_config = config or CaptureConfig()
        session = ListeningSession(session_id=session_id, config=session_config)
        self._sessions[session_id] = session

        streaming = StreamingTranscription(
            whisper_engine=self.whisper,
            config=session_config,
        )
        def record(segment):
            session.partial_results.append(segment.text)
            if callback:
                callback(segment)
        streaming.set_callback(record)

        session.streaming = streaming
        async def consume():
            async for _ in streaming.stream():
                verify_voice(decision, "voice_input", self.kill_switch)
        # Starting must finish successfully before a session is advertised.
        try:
            await streaming.start()
        except Exception:
            self._sessions.pop(session_id, None)
            raise
        session.task = asyncio.create_task(consume())
        logger.info(f"Started listening session: {session_id}")
        return session_id

    async def stop_listening(self, decision: dict, session_id: str) -> Optional[str]:
        """Stop a listening session and return final transcription."""
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"Session not found: {session_id}")
            return None

        if hasattr(session, 'task'):
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        if hasattr(session, 'streaming'):
            await session.streaming.stop()

        final_text = " ".join(session.partial_results) if session.partial_results else ""
        del self._sessions[session_id]
        logger.info(f"Stopped listening session: {session_id}")
        return final_text

    async def transcribe_file(self, decision: dict, file_path: str) -> str:
        """Transcribe an audio file."""
        verify_voice(decision, "voice_input", self.kill_switch)
        try:
            text = await self.whisper.transcribe_file_async(file_path)
            verify_voice(decision, "voice_input", self.kill_switch)
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
        queue = asyncio.Queue()
        def receive(segment):
            queue.put_nowait(segment)
            if callback:
                callback(segment)
        session_id = await self.start_listening(decision, callback=receive)
        try:
            while self.is_listening():
                yield await queue.get()
        finally:
            await self.stop_listening(decision, session_id)

    def get_session(self, session_id: str) -> Optional[ListeningSession]:
        """Get session by ID."""
        return self._sessions.get(session_id)

    def get_active_sessions(self) -> list:
        """Get all active session IDs."""
        return list(self._sessions.keys())

    def is_listening(self, session_id: Optional[str] = None) -> bool:
        """Check if session is active."""
        if session_id is None:
            return any(s.is_active and not s.task.done() for s in self._sessions.values())
        session = self._sessions.get(session_id)
        return session is not None and session.is_active and not session.task.done()

    def set_noise_profile(self, noise_audio: np.ndarray) -> None:
        """Set noise profile for filtering."""
        self.noise_detector.learn_noise_profile(noise_audio)

    def filter_audio(self, audio: np.ndarray) -> np.ndarray:
        """Filter noise from audio."""
        return self.noise_detector.filter_audio(audio)
