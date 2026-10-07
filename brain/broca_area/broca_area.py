"""Broca's Area - Main orchestrator for text-to-speech."""

import asyncio
import logging
from typing import Optional, List, Dict, AsyncGenerator, Union
from dataclasses import dataclass

from .tts_engine import TTSEngine, Voice
from .voice_selection import VoiceSelector, VoiceConfig
from .emotion_tone import EmotionTone, Emotion
from .streaming_speech import StreamingSpeech, SpeechSegment
from .exceptions import BrocaAreaError

logger = logging.getLogger(__name__)


@dataclass
class SpeakRequest:
    """Request for speech synthesis."""
    text: str
    emotion: Emotion = Emotion.NEUTRAL
    voice_id: Optional[str] = None
    stream: bool = False


class BrocaArea:
    """Orchestrator for text-to-speech processing."""

    def __init__(
        self,
        default_engine: str = "piper",
        default_voice: Optional[str] = None,
        language: str = "en",
    ):
        self.tts = TTSEngine(engine=default_engine, voice_id=default_voice)
        self.voice_selector = VoiceSelector(VoiceConfig(
            preferred_engine=default_engine,
            preferred_language=language,
        ))
        self.voice_selector.register_engine(default_engine, self.tts)
        self.emotion = EmotionTone()
        self.streaming = StreamingSpeech(tts_engine=self.tts, emotion_tone=self.emotion)
        self._is_speaking = False
        self._current_request: Optional[SpeakRequest] = None

    async def speak(
        self,
        decision: dict,
        text: str,
        emotion: Union[Emotion, str] = Emotion.NEUTRAL,
        voice_id: Optional[str] = None,
    ) -> str:
        """Synthesize speech and return audio file path."""
        if isinstance(emotion, str):
            emotion = Emotion(emotion.lower())

        if voice_id:
            self.voice_selector.select_voice(voice_id=voice_id)

        self.emotion.set_emotion(emotion)
        self._is_speaking = True
        self._current_request = SpeakRequest(text=text, emotion=emotion, voice_id=voice_id)

        try:
            output_path = self.tts.synthesize(text)
            logger.info(f"Speech synthesized: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Speech synthesis failed: {e}")
            raise BrocaAreaError(f"Synthesis failed: {e}")
        finally:
            self._is_speaking = False
            self._current_request = None

    async def stream_speech(
        self,
        decision: dict,
        text_stream: AsyncGenerator[str, None],
    ) -> AsyncGenerator[SpeechSegment, None]:
        """Stream speech from text generator."""
        self._is_speaking = True

        try:
            async for segment in self.streaming.speak_stream(text_stream):
                yield segment
        finally:
            self._is_speaking = False

    async def stop_speaking(self, decision: dict) -> None:
        """Stop current speech."""
        await self.streaming.interrupt()
        self._is_speaking = False
        logger.info("Speech stopped")

    def get_available_voices(self) -> List[Dict]:
        """Get list of available voices."""
        return self.voice_selector.list_voices()

    def set_voice(self, voice_id: str) -> bool:
        """Set active voice."""
        return self.voice_selector.select_voice(voice_id=voice_id) is not None

    def set_emotion(self, emotion: Union[Emotion, str]) -> bool:
        """Set default emotion."""
        return self.emotion.set_emotion(emotion)

    def get_emotion(self) -> Emotion:
        """Get current emotion."""
        return self.emotion.get_emotion()

    def is_speaking(self) -> bool:
        """Check if currently speaking."""
        return self._is_speaking

    def get_status(self) -> Dict:
        """Get current status."""
        return {
            "speaking": self._is_speaking,
            "current_voice": self.voice_selector.get_current_voice().id if self.voice_selector.get_current_voice() else None,
            "current_emotion": self.emotion.get_emotion().value,
            "engine": self.voice_selector.get_current_engine(),
            "available_engines": list(self.voice_selector._engines.keys()),
        }

    def register_engine(self, name: str, engine: TTSEngine) -> None:
        """Register additional TTS engine."""
        self.voice_selector.register_engine(name, engine)

    def set_audio_callback(self, callback) -> None:
        """Set audio playback callback."""
        self.streaming.set_audio_callback(callback)
