"""Wake word detector using Porcupine."""

import asyncio
import logging
import struct
from typing import Optional, Callable
from pathlib import Path

try:
    import pvporcupine
    import pyaudio
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logging.warning("Porcupine not available. Install pvporcupine and pyaudio.")

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """Detects wake word using Picovoice Porcupine."""

    def __init__(
        self,
        access_key: str,
        keywords: Optional[list] = None,
        keyword_paths: Optional[list] = None,
        sensitivities: Optional[list] = None,
        sample_rate: int = 16000,
        frame_length: int = 512,
    ):
        if not PORCUPINE_AVAILABLE:
            raise WakeWordError("Porcupine not installed")

        self.access_key = access_key
        self.keywords = keywords or ["hey google", "hey siri", "alexa"]
        self.keyword_paths = keyword_paths
        self.sensitivities = sensitivities or [0.5] * len(self.keywords)
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self._porcupine = None
        self._audio_stream = None
        self._pa = None
        self._is_listening = False
        self._callback: Optional[Callable[[int], None]] = None
        self._listen_task: Optional[asyncio.Task] = None

    def initialize(self) -> None:
        """Initialize Porcupine."""
        try:
            if self.keyword_paths:
                self._porcupine = pvporcupine.create(
                    access_key=self.access_key,
                    keyword_paths=self.keyword_paths,
                    sensitivities=self.sensitivities,
                )
            else:
                self._porcupine = pvporcupine.create(
                    access_key=self.access_key,
                    keywords=self.keywords,
                    sensitivities=self.sensitivities,
                )

            self._pa = pyaudio.PyAudio()
            self._audio_stream = self._pa.open(
                rate=self.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.frame_length,
            )
            logger.info(f"Wake word detector initialized with keywords: {self.keywords}")
        except Exception as e:
            logger.error(f"Failed to initialize wake word detector: {e}")
            raise WakeWordError(f"Initialization failed: {e}")

    def set_callback(self, callback: Callable[[int], None]) -> None:
        """Set callback for wake word detection."""
        self._callback = callback

    async def start(self) -> None:
        """Start wake word detection."""
        if self._is_listening:
            return

        if not self._porcupine:
            self.initialize()

        self._is_listening = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Wake word detection started")

    async def stop(self) -> None:
        """Stop wake word detection."""
        self._is_listening = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._audio_stream:
            self._audio_stream.stop_stream()
            self._audio_stream.close()
            self._audio_stream = None

        if self._pa:
            self._pa.terminate()
            self._pa = None

        logger.info("Wake word detection stopped")

    async def _listen_loop(self) -> None:
        """Main listening loop."""
        while self._is_listening:
            try:
                pcm = self._audio_stream.read(self.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * self.frame_length, pcm)
                keyword_index = self._porcupine.process(pcm)

                if keyword_index >= 0:
                    keyword = self.keywords[keyword_index] if keyword_index < len(self.keywords) else f"keyword_{keyword_index}"
                    logger.info(f"Wake word detected: {keyword}")
                    if self._callback:
                        self._callback(keyword_index)

            except Exception as e:
                logger.error(f"Wake word detection error: {e}")
                await asyncio.sleep(0.1)

    def is_listening(self) -> bool:
        """Check if listening."""
        return self._is_listening

    def get_keywords(self) -> list:
        """Get configured keywords."""
        return self.keywords

    def set_sensitivity(self, keyword_index: int, sensitivity: float) -> None:
        """Set sensitivity for a keyword."""
        if 0 <= keyword_index < len(self.sensitivities):
            self.sensitivities[keyword_index] = sensitivity
            if self._porcupine:
                self._porcupine.delete()
                self.initialize()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()


class WakeWordError(Exception):
    """Wake word detector error."""
    pass
