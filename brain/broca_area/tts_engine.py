"""TTS Engine - Text-to-Speech using Piper or Edge TTS."""

import asyncio
import logging
import tempfile
import os
from typing import Optional, List, Dict, Union
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from piper import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
    logger.warning("Piper TTS not available")

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("Edge TTS not available")


@dataclass
class Voice:
    """Represents a TTS voice."""
    id: str
    name: str
    language: str
    gender: str
    engine: str
    sample_rate: int = 22050


class TTSEngine:
    """Text-to-Speech engine supporting multiple backends."""

    def __init__(
        self,
        engine: str = "piper",
        voice_id: Optional[str] = None,
        sample_rate: int = 22050,
    ):
        self.engine = engine
        self.voice_id = voice_id
        self.sample_rate = sample_rate
        self._piper_voice = None
        self._available_voices: List[Voice] = []
        self._initialize()

    def _initialize(self) -> None:
        """Initialize TTS engine."""
        if self.engine == "piper" and PIPER_AVAILABLE:
            self._init_piper()
        elif self.engine == "edge" and EDGE_TTS_AVAILABLE:
            self._init_edge()
        else:
            raise TTSError(f"Engine {self.engine} not available")

    def _init_piper(self) -> None:
        """Initialize Piper TTS."""
        try:
            if self.voice_id:
                self._piper_voice = PiperVoice.load(self.voice_id)
            else:
                # Use default voice
                voices = self.get_available_voices()
                if voices:
                    self._piper_voice = PiperVoice.load(voices[0].id)
            logger.info("Piper TTS initialized")
        except Exception as e:
            logger.error(f"Piper init failed: {e}")
            raise TTSError(f"Piper initialization failed: {e}")

    def _init_edge(self) -> None:
        """Initialize Edge TTS."""
        logger.info("Edge TTS initialized")

    def get_available_voices(self) -> List[Voice]:
        """Get list of available voices."""
        if self._available_voices:
            return self._available_voices

        voices = []
        if self.engine == "piper" and PIPER_AVAILABLE:
            voices = self._get_piper_voices()
        elif self.engine == "edge" and EDGE_TTS_AVAILABLE:
            voices = self._get_edge_voices()

        self._available_voices = voices
        return voices

    def _get_piper_voices(self) -> List[Voice]:
        """Get Piper voices."""
        voices = []
        try:
            import piper
            from piper import VOICES
            for voice_id, voice_info in VOICES.items():
                voices.append(Voice(
                    id=voice_id,
                    name=voice_info.get("name", voice_id),
                    language=voice_info.get("language", "en"),
                    gender=voice_info.get("gender", "unknown"),
                    engine="piper",
                    sample_rate=voice_info.get("sample_rate", 22050),
                ))
        except Exception as e:
            logger.warning(f"Could not load Piper voices: {e}")
        return voices

    def _get_edge_voices(self) -> List[Voice]:
        """Get Edge TTS voices."""
        voices = []
        try:
            import asyncio
            voice_list = asyncio.run(edge_tts.list_voices())
            for v in voice_list:
                voices.append(Voice(
                    id=v["ShortName"],
                    name=v["FriendlyName"],
                    language=v["Locale"],
                    gender=v["Gender"],
                    engine="edge",
                    sample_rate=24000,
                ))
        except Exception as e:
            logger.warning(f"Could not load Edge voices: {e}")
        return voices

    def set_voice(self, voice_id: str) -> bool:
        """Set active voice."""
        voices = self.get_available_voices()
        for voice in voices:
            if voice.id == voice_id:
                self.voice_id = voice_id
                if self.engine == "piper" and PIPER_AVAILABLE:
                    self._piper_voice = PiperVoice.load(voice_id)
                logger.info(f"Voice set to: {voice_id}")
                return True
        logger.warning(f"Voice not found: {voice_id}")
        return False

    def synthesize(self, text: str, output_path: Optional[str] = None) -> str:
        """Synthesize text to speech."""
        if self.engine == "piper":
            return self._synthesize_piper(text, output_path)
        elif self.engine == "edge":
            return asyncio.run(self._synthesize_edge(text, output_path))
        else:
            raise TTSError(f"Unknown engine: {self.engine}")

    def _synthesize_piper(self, text: str, output_path: Optional[str] = None) -> str:
        """Synthesize using Piper."""
        if not self._piper_voice:
            raise TTSError("Piper voice not loaded")

        if output_path is None:
            output_path = tempfile.mktemp(suffix=".wav")

        try:
            self._piper_voice.synthesize(text, output_path)
            logger.info(f"Synthesized to: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}")
            raise TTSError(f"Synthesis failed: {e}")

    async def _synthesize_edge(self, text: str, output_path: Optional[str] = None) -> str:
        """Synthesize using Edge TTS."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".mp3")

        voice = self.voice_id or "en-US-AriaNeural"
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)
            logger.info(f"Synthesized to: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Edge TTS synthesis failed: {e}")
            raise TTSError(f"Synthesis failed: {e}")

    async def synthesize_streaming(self, text: str) -> asyncio.Queue:
        """Synthesize text and stream audio chunks."""
        queue = asyncio.Queue()

        async def producer():
            if self.engine == "piper":
                await self._stream_piper(text, queue)
            elif self.engine == "edge":
                await self._stream_edge(text, queue)
            await queue.put(None)

        asyncio.create_task(producer())
        return queue

    async def _stream_piper(self, text: str, queue: asyncio.Queue) -> None:
        """Stream Piper synthesis."""
        if not self._piper_voice:
            raise TTSError("Piper voice not loaded")

        # Piper doesn't natively support streaming, synthesize to temp file and stream
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name

        try:
            self._piper_voice.synthesize(text, temp_path)
            import soundfile as sf
            data, sr = sf.read(temp_path)
            chunk_size = sr // 10  # 100ms chunks
            for i in range(0, len(data), chunk_size):
                chunk = data[i:i + chunk_size]
                await queue.put(chunk)
                await asyncio.sleep(0.01)
        finally:
            os.unlink(temp_path)

    async def _stream_edge(self, text: str, queue: asyncio.Queue) -> None:
        """Stream Edge TTS synthesis."""
        voice = self.voice_id or "en-US-AriaNeural"
        communicate = edge_tts.Communicate(text, voice)

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                await queue.put(chunk["data"])
            await asyncio.sleep(0.01)

    def is_available(self) -> bool:
        """Check if engine is available."""
        if self.engine == "piper":
            return PIPER_AVAILABLE and self._piper_voice is not None
        elif self.engine == "edge":
            return EDGE_TTS_AVAILABLE
        return False


class TTSError(Exception):
    """TTS engine error."""
    pass
