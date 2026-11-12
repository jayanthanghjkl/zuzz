"""TTS backends with async synthesis, explicit readiness and owned temp files."""

import asyncio
import os
import re
import tempfile
import wave
from dataclasses import dataclass
from typing import List, Optional

try:
    from piper import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False


class TTSError(Exception):
    """Speech could not be generated; never report this as spoken."""


@dataclass
class Voice:
    """A configured backend voice."""

    id: str
    name: str
    language: str
    gender: str
    engine: str
    sample_rate: int = 22050


class TTSEngine:
    """Use Edge online or an explicitly configured local Piper ONNX model."""

    def __init__(self, engine: str = "edge", voice_id: Optional[str] = None,
                 sample_rate: int = 22050) -> None:
        """Configure without a remote voice-catalog request at startup."""
        self.engine, self.sample_rate = engine, sample_rate
        self.voice_id = voice_id or ("en-US-AriaNeural" if engine == "edge" else os.getenv("PIPER_MODEL", ""))
        self._piper_voice = None
        if engine == "edge" and not EDGE_TTS_AVAILABLE:
            raise TTSError("Install edge-tts or configure Piper for speech output")
        if engine == "piper":
            if not PIPER_AVAILABLE or not os.path.isfile(self.voice_id):
                raise TTSError("Piper requires piper-tts and PIPER_MODEL pointing to an ONNX voice")
            self._piper_voice = PiperVoice.load(self.voice_id)
        if engine not in ("edge", "piper"):
            raise TTSError(f"Unknown TTS engine: {engine}")

    def get_available_voices(self) -> List[Voice]:
        """Return configured voices without blocking on a network catalog."""
        language = "-".join(self.voice_id.split("-")[:2]) if self.engine == "edge" else "configured"
        return [Voice(self.voice_id, self.voice_id, language, "unspecified", self.engine)]

    def set_voice(self, voice_id: str) -> bool:
        """Validate and activate a voice; Piper selection requires a model file."""
        if self.engine == "edge":
            if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+Neural", voice_id):
                return False
        elif not os.path.isfile(voice_id):
            return False
        else:
            self._piper_voice = PiperVoice.load(voice_id)
        self.voice_id = voice_id
        return True

    async def synthesize_async(self, text: str, output_path: Optional[str] = None) -> str:
        """Generate speech without nesting event loops or blocking API I/O."""
        if self.engine == "edge":
            return await self._synthesize_edge(text, output_path)
        return await asyncio.to_thread(self._synthesize_piper, text, output_path)

    def synthesize(self, text: str, output_path: Optional[str] = None) -> str:
        """Synchronous entry for worker threads; async callers must await instead."""
        if self.engine == "piper":
            return self._synthesize_piper(text, output_path)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._synthesize_edge(text, output_path))
        raise TTSError("Use await synthesize_async() inside an async application")

    @staticmethod
    def _temp(suffix: str) -> str:
        """Securely allocate an output file owned by the caller."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return path

    def _synthesize_piper(self, text: str, output_path: Optional[str] = None) -> str:
        """Write a WAV using the Piper synthesize_wav API."""
        path = output_path or self._temp(".wav")
        try:
            with wave.open(path, "wb") as wav:
                self._piper_voice.synthesize_wav(text, wav)
            return path
        except Exception as exc:
            if output_path is None:
                os.unlink(path)
            raise TTSError(f"Piper synthesis failed: {exc}") from exc

    async def _synthesize_edge(self, text: str, output_path: Optional[str] = None) -> str:
        """Write Edge MP3 output; delete incomplete files on error/cancellation."""
        path = output_path or self._temp(".mp3")
        try:
            async with asyncio.timeout(45):
                await edge_tts.Communicate(text, self.voice_id).save(path)
            return path
        except BaseException as exc:
            if output_path is None:
                os.unlink(path)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise TTSError(f"Edge TTS unavailable (requires internet): {exc}") from exc

    async def synthesize_streaming(self, text: str) -> asyncio.Queue:
        """Queue encoded audio, always terminating even if synthesis fails."""
        queue = asyncio.Queue()

        async def producer() -> None:
            path = None
            try:
                path = await self.synthesize_async(text)
                with open(path, "rb") as audio:
                    while chunk := audio.read(4096):
                        await queue.put(chunk)
            except Exception as exc:
                await queue.put(exc)
            finally:
                if path:
                    os.unlink(path)
                await queue.put(None)

        asyncio.create_task(producer())
        return queue

    def is_available(self) -> bool:
        """Report installed/configured readiness, not network reachability."""
        return EDGE_TTS_AVAILABLE if self.engine == "edge" else self._piper_voice is not None
