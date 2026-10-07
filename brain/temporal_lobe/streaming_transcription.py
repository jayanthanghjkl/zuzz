"""Streaming transcription for real-time STT."""

import asyncio
import numpy as np
import logging
from typing import AsyncGenerator, Optional, Callable
from dataclasses import dataclass

from .whisper_engine import WhisperEngine
from .audio_capture import AudioCapture, CaptureConfig

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """Represents a transcription segment."""
    text: str
    start: float
    end: float
    is_partial: bool = False


class StreamingTranscription:
    """Real-time streaming transcription using Whisper."""

    def __init__(
        self,
        whisper_engine: Optional[WhisperEngine] = None,
        config: Optional[CaptureConfig] = None,
        chunk_duration: float = 2.0,
        overlap_duration: float = 0.5,
    ):
        self.whisper = whisper_engine or WhisperEngine(model_name="tiny")
        self.config = config or CaptureConfig()
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        self.audio_capture = AudioCapture(self.config)
        self._is_streaming = False
        self._buffer = np.array([], dtype=np.int16)
        self._last_text = ""
        self._callback: Optional[Callable[[TranscriptionSegment], None]] = None

    def set_callback(self, callback: Callable[[TranscriptionSegment], None]) -> None:
        """Set callback for transcription results."""
        self._callback = callback

    async def start(self) -> None:
        """Start streaming transcription."""
        if self._is_streaming:
            return
        self._is_streaming = True
        self._buffer = np.array([], dtype=np.int16)
        self._last_text = ""
        await self.audio_capture.start()
        logger.info("Streaming transcription started")

    async def stop(self) -> None:
        """Stop streaming transcription."""
        self._is_streaming = False
        await self.audio_capture.stop()
        logger.info("Streaming transcription stopped")

    async def stream(self) -> AsyncGenerator[TranscriptionSegment, None]:
        """Stream transcription results."""
        await self.start()
        try:
            chunk_samples = int(self.config.sample_rate * self.chunk_duration)
            overlap_samples = int(self.config.sample_rate * self.overlap_duration)

            async for chunk, is_speech in self.audio_capture.stream_with_vad():
                if not self._is_streaming:
                    break

                self._buffer = np.concatenate([self._buffer, chunk.flatten()])

                if len(self._buffer) >= chunk_samples:
                    segment = self._buffer[:chunk_samples]
                    self._buffer = self._buffer[chunk_samples - overlap_samples:]

                    if is_speech or len(segment) > 0:
                        text = await self._transcribe_segment(segment)
                        if text and text != self._last_text:
                            segment_result = TranscriptionSegment(
                                text=text,
                                start=0,
                                end=self.chunk_duration,
                                is_partial=True,
                            )
                            self._last_text = text
                            if self._callback:
                                self._callback(segment_result)
                            yield segment_result
        finally:
            await self.stop()

    async def _transcribe_segment(self, audio: np.ndarray) -> str:
        """Transcribe audio segment."""
        try:
            if audio.dtype == np.int16:
                audio = audio.astype(np.float32) / 32768.0
            result = await self.whisper.transcribe_async(audio)
            return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""

    async def transcribe_once(self, duration: float = 5.0) -> str:
        """Capture and transcribe once."""
        audio = await self.audio_capture.capture_fixed_duration(duration)
        if len(audio) == 0:
            return ""
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        result = await self.whisper.transcribe_async(audio)
        return result.get("text", "").strip()

    def is_streaming(self) -> bool:
        """Check if streaming."""
        return self._is_streaming
