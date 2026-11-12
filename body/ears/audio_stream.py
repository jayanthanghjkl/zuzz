"""Continuous audio stream for real-time processing."""

import asyncio
import numpy as np
from typing import AsyncGenerator, Optional, Callable
from collections import deque
import logging

from .microphone import Microphone

logger = logging.getLogger(__name__)


class AudioStream:
    """Manages continuous audio streaming with buffering."""

    def __init__(
        self,
        microphone: Optional[Microphone] = None,
        sample_rate: int = 16000,
        chunk_duration: float = 0.1,
        buffer_duration: float = 5.0,
    ):
        self.microphone = microphone or Microphone(sample_rate=sample_rate)
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.chunk_size = int(sample_rate * chunk_duration)
        self.buffer_duration = buffer_duration
        self.buffer_size = int(sample_rate * buffer_duration)
        self._buffer = deque(maxlen=self.buffer_size)
        self._is_streaming = False
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, int(buffer_duration / chunk_duration)))
        self._loop = None
        self._stream_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start audio streaming."""
        if self._is_streaming:
            return

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._queue.maxsize)
        self.microphone.set_callback(self._on_audio_data)
        self.microphone.start()
        self._is_streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())
        logger.info("Audio stream started")

    async def stop(self) -> None:
        """Stop audio streaming."""
        if not self._is_streaming:
            return

        self._is_streaming = False
        self.microphone.stop()
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        logger.info("Audio stream stopped")

    def _on_audio_data(self, data: np.ndarray) -> None:
        """Callback for incoming audio data."""
        if self._loop and self._is_streaming:
            try:
                self._loop.call_soon_threadsafe(self._enqueue, data.copy())
            except RuntimeError:
                return  # loop already closed during shutdown

    def _enqueue(self, data: np.ndarray) -> None:
        """Bound queue growth and mutate asyncio state only on its owner loop."""
        if not self._is_streaming:
            return
        self._buffer.extend(data.flatten())
        if self._queue.full():
            self._queue.get_nowait()  # drop oldest; retain current speech
        self._queue.put_nowait(data)

    async def _stream_loop(self) -> None:
        """Background task to process audio stream."""
        while self._is_streaming:
            await asyncio.sleep(self.chunk_duration)

    async def read_chunk(self) -> np.ndarray:
        """Read next audio chunk."""
        return await self._queue.get()

    async def stream_chunks(self) -> AsyncGenerator[np.ndarray, None]:
        """Async generator yielding audio chunks."""
        while self._is_streaming:
            chunk = await self.read_chunk()
            yield chunk

    def get_buffer(self) -> np.ndarray:
        """Get current audio buffer."""
        return np.array(self._buffer, dtype=np.int16)

    def clear_buffer(self) -> None:
        """Clear audio buffer."""
        self._buffer.clear()

    def is_streaming(self) -> bool:
        """Check if stream is active."""
        return self._is_streaming

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()