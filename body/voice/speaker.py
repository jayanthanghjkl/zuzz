"""Speaker - Audio playback via sounddevice."""

import sounddevice as sd
import numpy as np
import asyncio
import threading
import queue
import logging
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class Speaker:
    """Handles audio playback through speakers."""

    def __init__(
        self,
        device_id: Optional[int] = None,
        sample_rate: int = 22050,
        channels: int = 1,
        dtype: str = "float32",
        block_size: int = 1024,
    ):
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.block_size = block_size
        self._stream: Optional[sd.OutputStream] = None
        self._playback_queue: queue.Queue = queue.Queue()
        self._is_playing = False
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_callback: Optional[Callable] = None

    def list_devices(self) -> list:
        """List available audio output devices."""
        devices = sd.query_devices()
        output_devices = []
        for i, device in enumerate(devices):
            if device["max_output_channels"] > 0:
                output_devices.append({
                    "id": i,
                    "name": device["name"],
                    "channels": device["max_output_channels"],
                    "default_samplerate": device["default_samplerate"],
                })
        return output_devices

    def get_default_device(self) -> int:
        """Get default output device ID."""
        return sd.default.device[1] if sd.default.device[1] is not None else 0

    def play(self, audio_data: np.ndarray, blocking: bool = False) -> None:
        """Play audio data."""
        if blocking:
            self._play_blocking(audio_data)
        else:
            self._play_async(audio_data)

    def _play_blocking(self, audio_data: np.ndarray) -> None:
        """Play audio blocking until done."""
        try:
            sd.play(audio_data, samplerate=self.sample_rate, device=self.device_id)
            sd.wait()
        except Exception as e:
            logger.error(f"Playback error: {e}")
            raise SpeakerError(f"Playback failed: {e}")

    def _play_async(self, audio_data: np.ndarray) -> None:
        """Queue audio for async playback."""
        self._playback_queue.put(audio_data)
        if not self._is_playing:
            self._start_playback_thread()

    def _start_playback_thread(self) -> None:
        """Start background playback thread."""
        self._is_playing = True
        self._stop_event.clear()
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()

    def _playback_loop(self) -> None:
        """Background loop for continuous playback."""
        try:
            with sd.OutputStream(
                device=self.device_id,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.block_size,
                callback=self._stream_callback,
            ):
                while self._is_playing and not self._stop_event.is_set():
                    self._stop_event.wait(0.1)
        except Exception as e:
            logger.error(f"Playback loop error: {e}")
        finally:
            self._is_playing = False

    def _stream_callback(self, outdata, frames, time, status) -> None:
        """Stream callback for continuous playback."""
        if status:
            logger.warning(f"Playback status: {status}")

        try:
            audio_data = self._playback_queue.get_nowait()
            if len(audio_data) >= frames:
                outdata[:frames] = audio_data[:frames].reshape(-1, self.channels)
                if len(audio_data) > frames:
                    self._playback_queue.put(audio_data[frames:])
            else:
                outdata[:len(audio_data)] = audio_data.reshape(-1, self.channels)
                outdata[len(audio_data):] = 0
        except queue.Empty:
            outdata.fill(0)
            if self._playback_queue.empty():
                self._is_playing = False

    def stop(self) -> None:
        """Stop playback and clear queue."""
        self._stop_event.set()
        self._is_playing = False
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("Playback stopped")

    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._is_playing

    async def play_file(self, file_path: str) -> None:
        """Play audio file asynchronously."""
        import soundfile as sf
        try:
            data, sr = sf.read(file_path)
            if sr != self.sample_rate:
                import scipy.signal as signal
                data = signal.resample(data, int(len(data) * self.sample_rate / sr))
            self.play(data)
        except Exception as e:
            logger.error(f"Failed to play file: {e}")
            raise SpeakerError(f"Failed to play file: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()