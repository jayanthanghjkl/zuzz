"""Audio capture for Temporal Lobe."""

import asyncio
import numpy as np
import logging
from typing import Optional, Callable, AsyncGenerator
from dataclasses import dataclass

from body.ears.microphone import Microphone
from body.ears.audio_stream import AudioStream
from body.ears.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


@dataclass
class CaptureConfig:
    """Configuration for audio capture."""
    sample_rate: int = 16000
    chunk_duration: float = 0.1
    vad_enabled: bool = True
    vad_threshold: float = 0.01
    silence_duration: float = 0.5
    max_duration: float = 30.0


class AudioCapture:
    """Handles audio capture with VAD for speech detection."""

    def __init__(self, config: Optional[CaptureConfig] = None):
        self.config = config or CaptureConfig()
        self.microphone = Microphone(sample_rate=self.config.sample_rate)
        self.audio_stream = AudioStream(
            microphone=self.microphone,
            sample_rate=self.config.sample_rate,
            chunk_duration=self.config.chunk_duration,
        )
        self.vad = VoiceActivityDetector(
            sample_rate=self.config.sample_rate,
            energy_threshold=self.config.vad_threshold,
            silence_duration=self.config.silence_duration,
        ) if self.config.vad_enabled else None
        self._is_capturing = False
        self._capture_task: Optional[asyncio.Task] = None
        self._audio_buffer = []
        self._speech_detected = False

    async def start(self) -> None:
        """Start audio capture."""
        if self._is_capturing:
            return
        await self.audio_stream.start()
        self._is_capturing = True
        self._audio_buffer = []
        self._speech_detected = False
        logger.info("Audio capture started")

    async def stop(self) -> np.ndarray:
        """Stop audio capture and return recorded audio."""
        if not self._is_capturing:
            return np.array([], dtype=np.int16)

        await self.audio_stream.stop()
        self._is_capturing = False
        audio = np.array(self._audio_buffer, dtype=np.int16)
        self._audio_buffer = []
        logger.info(f"Audio capture stopped, recorded {len(audio)} samples")
        return audio

    async def capture_until_silence(self, max_duration: float = None) -> np.ndarray:
        """Capture audio until silence detected or max duration reached."""
        max_duration = max_duration or self.config.max_duration
        await self.start()

        start_time = asyncio.get_event_loop().time()
        silence_start = None

        async for chunk in self.audio_stream.stream_chunks():
            self._audio_buffer.extend(chunk.flatten())

            if self.vad:
                is_speech = self.vad.process_frame(chunk.flatten())
                if is_speech:
                    self._speech_detected = True
                    silence_start = None
                elif self._speech_detected and silence_start is None:
                    silence_start = asyncio.get_event_loop().time()
                elif self._speech_detected and silence_start:
                    if asyncio.get_event_loop().time() - silence_start > self.config.silence_duration:
                        break

            if asyncio.get_event_loop().time() - start_time > max_duration:
                logger.info("Max duration reached")
                break

        return await self.stop()

    async def capture_fixed_duration(self, duration: float) -> np.ndarray:
        """Capture audio for fixed duration."""
        await self.start()
        await asyncio.sleep(duration)
        return await self.stop()

    def get_captured_audio(self) -> np.ndarray:
        """Get currently captured audio without stopping."""
        return np.array(self._audio_buffer, dtype=np.int16)

    def is_capturing(self) -> bool:
        """Check if currently capturing."""
        return self._is_capturing

    def is_speech_detected(self) -> bool:
        """Check if speech has been detected in current capture."""
        return self._speech_detected

    async def stream_with_vad(self) -> AsyncGenerator[np.ndarray, None]:
        """Stream audio chunks with VAD info."""
        await self.start()
        try:
            async for chunk in self.audio_stream.stream_chunks():
                is_speech = False
                if self.vad:
                    is_speech = self.vad.process_frame(chunk.flatten())
                yield chunk, is_speech
        finally:
            await self.stop()
