"""Emotion tone control for TTS."""

import logging
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Emotion(Enum):
    """Supported emotions."""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    URGENT = "urgent"
    CALM = "calm"
    EXCITED = "excited"
    WHISPER = "whisper"


@dataclass
class EmotionParams:
    """Parameters for emotion modulation."""
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    emphasis: float = 1.0


DEFAULT_EMOTION_PARAMS = {
    Emotion.NEUTRAL: EmotionParams(rate=1.0, pitch=1.0, volume=1.0, emphasis=1.0),
    Emotion.HAPPY: EmotionParams(rate=1.1, pitch=1.15, volume=1.05, emphasis=1.2),
    Emotion.SAD: EmotionParams(rate=0.85, pitch=0.9, volume=0.9, emphasis=0.8),
    Emotion.ANGRY: EmotionParams(rate=1.15, pitch=1.1, volume=1.1, emphasis=1.3),
    Emotion.URGENT: EmotionParams(rate=1.25, pitch=1.05, volume=1.15, emphasis=1.4),
    Emotion.CALM: EmotionParams(rate=0.9, pitch=0.95, volume=0.95, emphasis=0.9),
    Emotion.EXCITED: EmotionParams(rate=1.2, pitch=1.2, volume=1.1, emphasis=1.3),
    Emotion.WHISPER: EmotionParams(rate=0.8, pitch=0.85, volume=0.5, emphasis=0.6),
}


class EmotionTone:
    """Controls emotional tone for TTS output."""

    def __init__(self):
        self._current_emotion = Emotion.NEUTRAL
        self._custom_params: Dict[Emotion, EmotionParams] = {}
        self._base_params = EmotionParams()

    def set_emotion(self, emotion: Union[Emotion, str]) -> bool:
        """Set current emotion."""
        if isinstance(emotion, str):
            try:
                emotion = Emotion(emotion.lower())
            except ValueError:
                logger.warning(f"Unknown emotion: {emotion}")
                return False

        self._current_emotion = emotion
        logger.info(f"Emotion set to: {emotion.value}")
        return True

    def get_emotion(self) -> Emotion:
        """Get current emotion."""
        return self._current_emotion

    def get_params(self, emotion: Optional[Emotion] = None) -> EmotionParams:
        """Get parameters for emotion."""
        emotion = emotion or self._current_emotion

        if emotion in self._custom_params:
            return self._custom_params[emotion]

        base = DEFAULT_EMOTION_PARAMS.get(emotion, DEFAULT_EMOTION_PARAMS[Emotion.NEUTRAL])

        return EmotionParams(
            rate=base.rate * self._base_params.rate,
            pitch=base.pitch * self._base_params.pitch,
            volume=base.volume * self._base_params.volume,
            emphasis=base.emphasis * self._base_params.emphasis,
        )

    def set_custom_params(self, emotion: Emotion, params: EmotionParams) -> None:
        """Set custom parameters for an emotion."""
        self._custom_params[emotion] = params
        logger.info(f"Custom params set for {emotion.value}")

    def set_base_params(self, params: EmotionParams) -> None:
        """Set base parameters applied to all emotions."""
        self._base_params = params

    def apply_to_text(self, text: str, emotion: Optional[Emotion] = None) -> str:
        """Apply emotion markers to text (for engines that support SSML)."""
        emotion = emotion or self._current_emotion
        params = self.get_params(emotion)

        if emotion == Emotion.WHISPER:
            return f"<amazon:effect name=\"whispered\">{text}</amazon:effect>"
        elif emotion == Emotion.URGENT:
            return f"<prosody rate=\"{params.rate:.2f}\" pitch=\"{params.pitch:.2f}\" volume=\"{params.volume:.2f}\">{text}</prosody>"
        elif emotion in (Emotion.HAPPY, Emotion.EXCITED):
            return f"<prosody rate=\"{params.rate:.2f}\" pitch=\"{params.pitch:.2f}\" volume=\"{params.volume:.2f}\">{text}</prosody>"
        elif emotion in (Emotion.SAD, Emotion.CALM):
            return f"<prosody rate=\"{params.rate:.2f}\" pitch=\"{params.pitch:.2f}\" volume=\"{params.volume:.2f}\">{text}</prosody>"
        elif emotion == Emotion.ANGRY:
            return f"<prosody rate=\"{params.rate:.2f}\" pitch=\"{params.pitch:.2f}\" volume=\"{params.volume:.2f}\">{text}</prosody>"

        return text

    def get_ssml_params(self, emotion: Optional[Emotion] = None) -> Dict[str, Any]:
        """Get SSML-compatible parameters."""
        params = self.get_params(emotion)
        return {
            "rate": f"{params.rate:.2f}",
            "pitch": f"{params.pitch:.2f}",
            "volume": f"{params.volume:.2f}",
        }

    def list_emotions(self) -> list:
        """List available emotions."""
        return [e.value for e in Emotion]

    def reset(self) -> None:
        """Reset to neutral emotion."""
        self._current_emotion = Emotion.NEUTRAL
        self._custom_params = {}
        self._base_params = EmotionParams()


class EmotionError(Exception):
    """Emotion tone error."""
    pass
