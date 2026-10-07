"""Voice selection for TTS."""

import logging
from typing import Optional, List, Dict
from dataclasses import dataclass

from .tts_engine import TTSEngine, Voice

logger = logging.getLogger(__name__)


@dataclass
class VoiceConfig:
    """Configuration for voice selection."""
    preferred_engine: str = "piper"
    preferred_language: str = "en"
    preferred_gender: Optional[str] = None
    fallback_voice: Optional[str] = None


class VoiceSelector:
    """Selects and manages TTS voices."""

    def __init__(self, config: Optional[VoiceConfig] = None):
        self.config = config or VoiceConfig()
        self._engines: Dict[str, TTSEngine] = {}
        self._current_engine: Optional[str] = None
        self._current_voice: Optional[Voice] = None

    def register_engine(self, name: str, engine: TTSEngine) -> None:
        """Register a TTS engine."""
        self._engines[name] = engine
        if self._current_engine is None:
            self._current_engine = name
        logger.info(f"Registered TTS engine: {name}")

    def get_engine(self, name: Optional[str] = None) -> Optional[TTSEngine]:
        """Get engine by name or current engine."""
        name = name or self._current_engine
        return self._engines.get(name) if name else None

    def set_engine(self, name: str) -> bool:
        """Set active engine."""
        if name in self._engines:
            self._current_engine = name
            self._current_voice = None
            logger.info(f"Active engine set to: {name}")
            return True
        return False

    def get_all_voices(self) -> List[Voice]:
        """Get all voices from all engines."""
        all_voices = []
        for engine in self._engines.values():
            all_voices.extend(engine.get_available_voices())
        return all_voices

    def get_voices_by_language(self, language: str) -> List[Voice]:
        """Get voices for a specific language."""
        return [v for v in self.get_all_voices() if v.language.startswith(language)]

    def get_voices_by_engine(self, engine: str) -> List[Voice]:
        """Get voices for a specific engine."""
        eng = self._engines.get(engine)
        return eng.get_available_voices() if eng else []

    def select_voice(
        self,
        voice_id: Optional[str] = None,
        language: Optional[str] = None,
        gender: Optional[str] = None,
        engine: Optional[str] = None,
    ) -> Optional[Voice]:
        """Select best matching voice."""
        voices = self.get_all_voices()

        if engine:
            voices = [v for v in voices if v.engine == engine]
        elif self.config.preferred_engine:
            voices = [v for v in voices if v.engine == self.config.preferred_engine]

        if voice_id:
            for v in voices:
                if v.id == voice_id:
                    self._current_voice = v
                    self._current_engine = v.engine
                    self._engines[v.engine].set_voice(voice_id)
                    return v

        if language:
            voices = [v for v in voices if v.language.startswith(language)]
        elif self.config.preferred_language:
            voices = [v for v in voices if v.language.startswith(self.config.preferred_language)]

        if gender:
            voices = [v for v in voices if v.gender.lower() == gender.lower()]
        elif self.config.preferred_gender:
            voices = [v for v in voices if v.gender.lower() == self.config.preferred_gender.lower()]

        if voices:
            self._current_voice = voices[0]
            self._current_engine = voices[0].engine
            self._engines[voices[0].engine].set_voice(voices[0].id)
            return voices[0]

        if self.config.fallback_voice:
            return self.select_voice(voice_id=self.config.fallback_voice)

        return None

    def get_current_voice(self) -> Optional[Voice]:
        """Get currently selected voice."""
        return self._current_voice

    def get_current_engine(self) -> Optional[str]:
        """Get currently active engine."""
        return self._current_engine

    def list_voices(self) -> List[Dict]:
        """List all voices as dictionaries."""
        return [
            {
                "id": v.id,
                "name": v.name,
                "language": v.language,
                "gender": v.gender,
                "engine": v.engine,
                "sample_rate": v.sample_rate,
                "current": v.id == self._current_voice.id if self._current_voice else False,
            }
            for v in self.get_all_voices()
        ]


class VoiceSelectionError(Exception):
    """Voice selection error."""
    pass
