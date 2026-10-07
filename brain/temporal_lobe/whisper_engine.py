"""Whisper STT Engine - Speech-to-Text using OpenAI Whisper."""

import whisper
import numpy as np
import torch
import asyncio
import logging
from typing import Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)


class WhisperEngine:
    """Whisper-based Speech-to-Text engine."""

    def __init__(
        self,
        model_name: str = "tiny",
        device: Optional[str] = None,
        language: Optional[str] = None,
        fp16: bool = False,
    ):
        self.model_name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.language = language
        self.fp16 = fp16 and self.device == "cuda"
        self._model = None
        self._load_model()

    def _load_model(self) -> None:
        """Load Whisper model."""
        try:
            logger.info(f"Loading Whisper model: {self.model_name} on {self.device}")
            self._model = whisper.load_model(self.model_name, device=self.device)
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise WhisperError(f"Failed to load model: {e}")

    def transcribe(
        self,
        audio: Union[np.ndarray, str, Path],
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> dict:
        """
        Transcribe audio to text.
        
        Args:
            audio: Audio data as numpy array or file path
            language: Language code (optional)
            task: "transcribe" or "translate"
            
        Returns:
            Dictionary with transcription results
        """
        if self._model is None:
            raise WhisperError("Model not loaded")

        try:
            if isinstance(audio, (str, Path)):
                result = self._model.transcribe(
                    str(audio),
                    language=language or self.language,
                    task=task,
                    fp16=self.fp16,
                )
            else:
                result = self._model.transcribe(
                    audio,
                    language=language or self.language,
                    task=task,
                    fp16=self.fp16,
                )
            return result
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise WhisperError(f"Transcription failed: {e}")

    async def transcribe_async(
        self,
        audio: Union[np.ndarray, str, Path],
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> dict:
        """Transcribe audio asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.transcribe, audio, language, task)

    def transcribe_file(self, file_path: Union[str, Path]) -> str:
        """Transcribe audio file and return text only."""
        result = self.transcribe(file_path)
        return result.get("text", "").strip()

    async def transcribe_file_async(self, file_path: Union[str, Path]) -> str:
        """Transcribe audio file asynchronously."""
        result = await self.transcribe_async(file_path)
        return result.get("text", "").strip()

    def detect_language(self, audio: np.ndarray) -> str:
        """Detect language of audio."""
        if self._model is None:
            raise WhisperError("Model not loaded")
        try:
            mel = whisper.log_mel_spectrogram(audio).to(self.device)
            _, probs = self._model.detect_language(mel)
            return max(probs, key=probs.get)
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            raise WhisperError(f"Language detection failed: {e}")

    @property
    def model(self):
        """Get underlying Whisper model."""
        return self._model

    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._model is not None


class WhisperError(Exception):
    """Whisper engine error."""
    pass
