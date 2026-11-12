"""Streaming speech synthesis for real-time TTS."""

import asyncio
import logging
from typing import AsyncGenerator, Optional, Callable, Union
from dataclasses import dataclass
import queue
import threading

from .tts_engine import TTSEngine
from .emotion_tone import EmotionTone, Emotion

logger = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    """Represents a segment of streaming speech."""
    text: str
    audio_chunk: Optional[bytes] = None
    is_final: bool = False
    emotion: Emotion = Emotion.NEUTRAL


class StreamingSpeech:
    """Streams speech output as text is generated."""

    def __init__(
        self,
        tts_engine: Optional[TTSEngine] = None,
        emotion_tone: Optional[EmotionTone] = None,
        chunk_size: int = 1024,
    ):
        self.tts = tts_engine or TTSEngine()
        self.emotion = emotion_tone or EmotionTone()
        self.chunk_size = chunk_size
        self._is_speaking = False
        self._speech_queue: asyncio.Queue = asyncio.Queue()
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._audio_callback: Optional[Callable[[bytes], None]] = None

    def set_audio_callback(self, callback: Callable[[bytes], None]) -> None:
        """Set callback for audio playback."""
        self._audio_callback = callback

    async def start(self) -> None:
        """Start streaming speech."""
        self._is_speaking = True
        self._stop_event.clear()
        logger.info("Streaming speech started")

    async def stop(self) -> None:
        """Stop streaming speech."""
        self._is_speaking = False
        self._stop_event.set()
        await self._speech_queue.put(None)
        logger.info("Streaming speech stopped")

    async def speak_stream(self, text_stream: AsyncGenerator[str, None]) -> AsyncGenerator[SpeechSegment, None]:
        """Stream speech from text generator."""
        await self.start()
        buffer = ""
        sentence_endings = {".", "!", "?", "。", "！", "？"}

        try:
            async for text_chunk in text_stream:
                if not self._is_speaking:
                    break

                buffer += text_chunk

                # Check for sentence endings
                for ending in sentence_endings:
                    if ending in buffer:
                        sentences = buffer.split(ending)
                        for sentence in sentences[:-1]:
                            if sentence.strip():
                                segment = await self._speak_segment(sentence.strip() + ending)
                                yield segment
                        buffer = sentences[-1]
                        break

            # Speak remaining buffer
            if buffer.strip() and self._is_speaking:
                segment = await self._speak_segment(buffer.strip())
                yield segment

        finally:
            await self.stop()

    async def _speak_segment(self, text: str) -> SpeechSegment:
        """Speak a single text segment."""
        emotion = self.emotion.get_emotion()

        audio_queue = await self.tts.synthesize_streaming(text)

        segment = SpeechSegment(text=text, emotion=emotion)

        async def playback_consumer():
            while True:
                chunk = await audio_queue.get()
                if isinstance(chunk, Exception):
                    raise chunk
                if chunk is None:
                    break
                if self._audio_callback and self._is_speaking:
                    self._audio_callback(chunk)

        await playback_consumer()

        return segment

    async def speak_text(self, text: str, emotion: Optional[Emotion] = None) -> SpeechSegment:
        """Speak complete text at once."""
        if emotion:
            self.emotion.set_emotion(emotion)

        from pathlib import Path
        output_path = await self.tts.synthesize_async(text)
        try:
            audio = Path(output_path).read_bytes()
            if self._audio_callback:
                # The streaming callback consistently receives encoded MP3/WAV,
                # never unlabelled PCM mixed with encoded frames.
                self._audio_callback(audio)
            return SpeechSegment(text=text, audio_chunk=audio, is_final=True,
                                 emotion=self.emotion.get_emotion())
        finally:
            Path(output_path).unlink(missing_ok=True)

    async def interrupt(self) -> None:
        """Interrupt current speech."""
        self._is_speaking = False
        self._stop_event.set()
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("Speech interrupted")

    def is_speaking(self) -> bool:
        """Check if currently speaking."""
        return self._is_speaking

    def set_emotion(self, emotion: Union[Emotion, str]) -> None:
        """Set emotion for subsequent speech."""
        self.emotion.set_emotion(emotion)


class StreamingError(Exception):
    """Streaming speech error."""
    pass
