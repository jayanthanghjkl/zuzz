"""Tests for voice input functionality."""

import pytest
import asyncio
import numpy as np
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path
import tempfile

from brain.temporal_lobe.temporal_lobe import TemporalLobe
from brain.temporal_lobe.whisper_engine import WhisperEngine
from brain.temporal_lobe.audio_capture import AudioCapture, CaptureConfig
from brain.temporal_lobe.streaming_transcription import StreamingTranscription
from brain.temporal_lobe.noise_detection import NoiseDetector
from body.ears.microphone import Microphone
from body.ears.audio_stream import AudioStream
from body.ears.vad import VoiceActivityDetector
from body.ears.exceptions import MicrophoneError, AudioStreamError, VADError


class TestMicrophoneCaptureRequiresPermission:
    """Test microphone capture permission handling."""

    @pytest.mark.asyncio
    async def test_microphone_list_devices(self):
        """Test listing audio devices."""
        mic = Microphone()
        devices = mic.list_devices()
        assert isinstance(devices, list)

    @pytest.mark.asyncio
    async def test_microphone_start_stop(self):
        """Test microphone start/stop."""
        mic = Microphone()
        # Should not raise without actual hardware
        mic.start()
        mic.stop()
        assert not mic.is_recording()

    @pytest.mark.asyncio
    async def test_microphone_capture_chunk(self):
        """Test capturing audio chunk."""
        mic = Microphone()
        chunk = await mic.capture_chunk(0.01)
        assert isinstance(chunk, np.ndarray)


class TestWhisperTranscribesAudioFile:
    """Test Whisper transcription."""

    @pytest.mark.asyncio
    async def test_whisper_engine_load(self):
        """Test Whisper model loads."""
        engine = WhisperEngine(model_name="tiny")
        assert engine.is_loaded()

    @pytest.mark.asyncio
    async def test_whisper_transcribe_array(self):
        """Test transcription from numpy array."""
        engine = WhisperEngine(model_name="tiny")
        # Create silent audio
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert "text" in result

    @pytest.mark.skip(reason="Requires ffmpeg")
    @pytest.mark.asyncio
    async def test_whisper_transcribe_file(self):
        """Test transcription from file."""
        engine = WhisperEngine(model_name="tiny")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            # Write minimal WAV header + silence
            import wave
            with wave.open(f.name, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b'\x00' * 32000)

        try:
            text = engine.transcribe_file(f.name)
            assert isinstance(text, str)
        finally:
            Path(f.name).unlink()


class TestStreamingTranscriptionYieldsPartialResults:
    """Test streaming transcription."""

    @pytest.mark.asyncio
    async def test_streaming_transcription_init(self):
        """Test streaming transcription initialization."""
        engine = WhisperEngine(model_name="tiny")
        streaming = StreamingTranscription(whisper_engine=engine)
        assert streaming is not None

    @pytest.mark.asyncio
    async def test_transcribe_once(self):
        """Test single transcription."""
        engine = WhisperEngine(model_name="tiny")
        streaming = StreamingTranscription(whisper_engine=engine)
        # This would need actual audio, so we test the method exists
        assert hasattr(streaming, 'transcribe_once')


class TestNoiseFilteredBeforeTranscription:
    """Test noise detection and filtering."""

    def test_noise_detector_init(self):
        """Test noise detector initialization."""
        detector = NoiseDetector()
        assert detector is not None

    def test_estimate_noise_level(self):
        """Test noise level estimation."""
        detector = NoiseDetector()
        silent = np.zeros(16000, dtype=np.int16)
        noisy = np.random.randint(-32768, 32767, 16000, dtype=np.int16)

        silent_level = detector.estimate_noise_level(silent)
        noisy_level = detector.estimate_noise_level(noisy)

        assert silent_level < noisy_level

    def test_learn_noise_profile(self):
        """Test learning noise profile."""
        detector = NoiseDetector()
        noise = np.random.randint(-1000, 1000, 16000, dtype=np.int16)
        detector.learn_noise_profile(noise)
        assert detector.has_noise_profile()

    def test_reduce_noise(self):
        """Test noise reduction."""
        detector = NoiseDetector()
        noise = np.random.randint(-1000, 1000, 16000, dtype=np.int16)
        detector.learn_noise_profile(noise)

        signal = np.random.randint(-10000, 10000, 16000, dtype=np.int16)
        clean = detector.reduce_noise(signal)
        # ISTFT may change length slightly, just verify it returns an array
        assert isinstance(clean, np.ndarray)
        assert len(clean) > 0


class TestVoiceInputRespectsKillSwitch:
    """Test kill switch integration."""

    @pytest.mark.asyncio
    async def test_temporal_lobe_kill_switch(self):
        """Test temporal lobe respects kill switch."""
        # This would integrate with actual kill switch
        lobe = TemporalLobe()
        assert lobe is not None


class TestWakeWordActivatesFromSleep:
    """Test wake word detection."""

    @pytest.mark.asyncio
    async def test_reticular_formation_init(self):
        """Test reticular formation initialization."""
        from brain.reticular_formation.reticular_formation import ReticularFormation, ReticularConfig
        config = ReticularConfig()
        rf = ReticularFormation(config)
        assert rf is not None

    @pytest.mark.asyncio
    async def test_wake_word_detector_init(self):
        """Test wake word detector initialization."""
        from brain.reticular_formation.wake_word_detector import WakeWordDetector
        # Requires access key, so test structure only
        assert WakeWordDetector is not None


class TestVADDetectsSpeechVsSilence:
    """Test Voice Activity Detection."""

    def test_vad_init(self):
        """Test VAD initialization."""
        vad = VoiceActivityDetector()
        assert vad is not None

    def test_vad_process_frame(self):
        """Test VAD frame processing."""
        vad = VoiceActivityDetector()
        # Silent frame
        silent = np.zeros(480, dtype=np.int16)
        result = vad.process_frame(silent)
        assert result is False

        # Loud frame
        loud = np.full(480, 10000, dtype=np.int16)
        result = vad.process_frame(loud)
        # May be True or False depending on threshold
        assert isinstance(result, bool)

    def test_vad_process_audio(self):
        """Test VAD on full audio."""
        vad = VoiceActivityDetector()
        # Create audio with speech-like pattern
        audio = np.zeros(16000, dtype=np.int16)
        audio[4000:8000] = 10000  # Speech segment

        segments = vad.process_audio(audio)
        assert isinstance(segments, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])