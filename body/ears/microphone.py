"""Microphone hardware access via sounddevice."""

import sounddevice as sd
import numpy as np
import asyncio
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)


class Microphone:
    """Handles microphone hardware access and audio capture."""

    def __init__(
        self,
        device_id: Optional[int] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: str = "int16",
        block_size: int = 1024,
    ):
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.block_size = block_size
        self._stream: Optional[sd.InputStream] = None
        self._callback: Optional[Callable[[np.ndarray], None]] = None
        self._is_recording = False

    def list_devices(self) -> list:
        """List available audio input devices."""
        devices = sd.query_devices()
        input_devices = []
        for i, device in enumerate(devices):
            if device["max_input_channels"] > 0:
                input_devices.append({
                    "id": i,
                    "name": device["name"],
                    "channels": device["max_input_channels"],
                    "default_samplerate": device["default_samplerate"],
                })
        return input_devices

    def get_default_device(self) -> int:
        """Get default input device ID."""
        return sd.default.device[0] if sd.default.device[0] is not None else 0

    def set_callback(self, callback: Callable[[np.ndarray], None]) -> None:
        """Set callback for audio data."""
        self._callback = callback

    def start(self) -> None:
        """Start microphone capture."""
        if self._is_recording:
            logger.warning("Microphone already recording")
            return

        try:
            self._stream = sd.InputStream(
                device=self.device_id,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.block_size,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._is_recording = True
            logger.info(f"Microphone started on device {self.device_id or 'default'}")
        except Exception as e:
            logger.error(f"Failed to start microphone: {e}")
            raise MicrophoneError(f"Failed to start microphone: {e}")

    def stop(self) -> None:
        """Stop microphone capture."""
        if not self._is_recording:
            return

        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._is_recording = False
            logger.info("Microphone stopped")
        except Exception as e:
            logger.error(f"Failed to stop microphone: {e}")
            raise MicrophoneError(f"Failed to stop microphone: {e}")

    def _audio_callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        """Internal callback for audio data."""
        if status:
            logger.warning(f"Audio callback status: {status}")
        if self._callback is not None and self._is_recording:
            self._callback(indata.copy())

    def is_recording(self) -> bool:
        """Check if microphone is recording."""
        return self._is_recording

    async def capture_chunk(self, duration: float = 0.1) -> np.ndarray:
        """Capture a single chunk of audio asynchronously."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        chunk = []

        def callback(indata, frames, time, status):
            chunk.append(indata.copy())
            if not future.done():
                future.set_result(None)

        with sd.InputStream(
            device=self.device_id,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
            blocksize=int(self.sample_rate * duration),
            callback=callback,
        ):
            await future

        return chunk[0] if chunk else np.array([], dtype=self.dtype)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()