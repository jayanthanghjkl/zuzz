"""Audio mixer for combining multiple audio sources."""

import numpy as np
import threading
import queue
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from queue import Queue

logger = logging.getLogger(__name__)


@dataclass
class AudioSource:
    """Represents an audio source in the mixer."""
    name: str
    sample_rate: int
    channels: int
    volume: float = 1.0
    enabled: bool = True
    queue: Queue = field(default_factory=Queue)

    def __post_init__(self):
        if self.queue is None:
            self.queue = Queue()


class AudioMixer:
    """Mixes multiple audio sources into single output."""

    def __init__(
        self,
        sample_rate: int = 22050,
        channels: int = 1,
        block_size: int = 1024,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self._sources: Dict[str, AudioSource] = {}
        self._master_volume = 1.0
        self._mix_thread: Optional[threading.Thread] = None
        self._output_queue: queue.Queue = queue.Queue(maxsize=100)
        self._stop_event = threading.Event()
        self._is_mixing = False

    def add_source(
        self,
        name: str,
        sample_rate: int = None,
        channels: int = None,
        volume: float = 1.0,
    ) -> None:
        """Add an audio source to the mixer."""
        if name in self._sources:
            logger.warning(f"Source {name} already exists, updating")
        self._sources[name] = AudioSource(
            name=name,
            sample_rate=sample_rate or self.sample_rate,
            channels=channels or self.channels,
            volume=volume,
        )
        logger.info(f"Added audio source: {name}")

    def remove_source(self, name: str) -> None:
        """Remove an audio source."""
        if name in self._sources:
            del self._sources[name]
            logger.info(f"Removed audio source: {name}")

    def set_source_volume(self, name: str, volume: float) -> None:
        """Set volume for a specific source (0.0 to 1.0)."""
        if name in self._sources:
            self._sources[name].volume = max(0.0, min(1.0, volume))

    def set_master_volume(self, volume: float) -> None:
        """Set master volume (0.0 to 1.0)."""
        self._master_volume = max(0.0, min(1.0, volume))

    def enable_source(self, name: str, enabled: bool = True) -> None:
        """Enable or disable a source."""
        if name in self._sources:
            self._sources[name].enabled = enabled

    def feed_audio(self, name: str, audio_data: np.ndarray) -> bool:
        """Feed audio data to a source."""
        if name not in self._sources:
            return False
        source = self._sources[name]
        if not source.enabled:
            return False
        try:
            source.queue.put_nowait(audio_data.copy())
            return True
        except queue.Full:
            logger.warning(f"Source {name} queue full, dropping audio")
            return False

    def start(self) -> None:
        """Start mixing thread."""
        if self._is_mixing:
            return
        self._is_mixing = True
        self._stop_event.clear()
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mix_thread.start()
        logger.info("Audio mixer started")

    def stop(self) -> None:
        """Stop mixing thread."""
        self._is_mixing = False
        self._stop_event.set()
        if self._mix_thread:
            self._mix_thread.join(timeout=1.0)
        logger.info("Audio mixer stopped")

    def _mix_loop(self) -> None:
        """Main mixing loop."""
        while self._is_mixing and not self._stop_event.is_set():
            mixed = self._mix_block()
            try:
                self._output_queue.put_nowait(mixed)
            except queue.Full:
                pass
            self._stop_event.wait(self.block_size / self.sample_rate)

    def _mix_block(self) -> np.ndarray:
        """Mix one block of audio from all sources."""
        mixed = np.zeros((self.block_size, self.channels), dtype=np.float32)
        active_sources = 0

        for source in self._sources.values():
            if not source.enabled:
                continue

            try:
                audio = source.queue.get_nowait()
                if len(audio) >= self.block_size:
                    chunk = audio[:self.block_size]
                    if len(audio) > self.block_size:
                        source.queue.put(audio[self.block_size:])
                else:
                    chunk = np.zeros((self.block_size, self.channels), dtype=np.float32)
                    chunk[:len(audio)] = audio.reshape(-1, self.channels)

                if source.sample_rate != self.sample_rate:
                    chunk = self._resample(chunk, source.sample_rate)

                mixed += chunk * source.volume
                active_sources += 1
            except queue.Empty:
                continue

        if active_sources > 0:
            mixed /= active_sources
        mixed *= self._master_volume
        np.clip(mixed, -1.0, 1.0, out=mixed)
        return mixed

    def _resample(self, audio: np.ndarray, orig_sr: int) -> np.ndarray:
        """Resample audio to mixer sample rate."""
        if orig_sr == self.sample_rate:
            return audio
        import scipy.signal as signal
        num_samples = int(len(audio) * self.sample_rate / orig_sr)
        return signal.resample(audio, num_samples, axis=0)

    def get_output(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """Get mixed output block."""
        try:
            return self._output_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_mixing(self) -> bool:
        """Check if mixer is running."""
        return self._is_mixing

    def get_source_names(self) -> List[str]:
        """Get list of source names."""
        return list(self._sources.keys())