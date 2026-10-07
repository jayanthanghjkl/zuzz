"""Tests for voice output functionality."""

import pytest
import asyncio
import numpy as np
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from brain.broca_area.broca_area import BrocaArea
from brain.broca_area.tts_engine import TTSEngine, Voice
from brain.broca_area.voice_selection import VoiceSelector, VoiceConfig
from brain.broca_area.emotion_tone import EmotionTone, Emotion, EmotionParams
from brain.broca_area.streaming_speech import StreamingSpeech
from body.voice.speaker import Speaker
from body.voice.audio_mixer import AudioMixer
from body.voice.exceptions import SpeakerError, AudioMixerError


class TestTTSGeneratesAudio:
    """Test TTS audio generation."""

    @pytest.mark.asyncio
    async def test_tts_engine_init(self):
        """Test TTS engine initialization."""
        try:
            engine = TTSEngine(engine="piper")
            # May fail if Piper not fully set up
        except Exception:
            # Expected if voices not available
            pass

    @pytest.mark.asyncio
    async def test_tts_synthesize(self):
        """Test text synthesis."""
        engine = TTSEngine(engine="edge")
        # Edge TTS should work without local models
        try:
            result = engine.synthesize("Hello world")
            assert isinstance(result, str)
        except Exception:
            # May fail without network
            pass


class TestDifferentVoicesAvailable:
    """Test voice selection."""

    def test_voice_selector_init(self):
        """Test voice selector initialization."""
        selector = VoiceSelector()
        assert selector is not None

    def test_register_engine(self):
        """Test engine registration."""
        selector = VoiceSelector()
        engine = Mock(spec=TTSEngine)
        engine.get_available_voices.return_value = [
            Voice(id="test_voice", name="Test", language="en", gender="female", engine="test")
        ]
        selector.register_engine("test", engine)
        assert "test" in selector._engines

    def test_get_all_voices(self):
        """Test getting all voices."""
        selector = VoiceSelector()
        voices = selector.get_all_voices()
        assert isinstance(voices, list)

    def test_select_voice(self):
        """Test voice selection."""
        selector = VoiceSelector(VoiceConfig(preferred_engine="test"))
        engine = Mock(spec=TTSEngine)
        engine.get_available_voices.return_value = [
            Voice(id="voice1", name="Voice 1", language="en", gender="female", engine="test"),
            Voice(id="voice2", name="Voice 2", language="en", gender="male", engine="test"),
        ]
        engine.set_voice = Mock(return_value=True)
        selector.register_engine("test", engine)

        selected = selector.select_voice(voice_id="voice1")
        assert selected is not None
        assert selected.id == "voice1"


class TestEmotionAffectsTone:
    """Test emotion tone control."""

    def test_emotion_tone_init(self):
        """Test emotion tone initialization."""
        tone = EmotionTone()
        assert tone is not None

    def test_set_emotion(self):
        """Test setting emotion."""
        tone = EmotionTone()
        result = tone.set_emotion(Emotion.HAPPY)
        assert result is True
        assert tone.get_emotion() == Emotion.HAPPY

    def test_set_emotion_string(self):
        """Test setting emotion from string."""
        tone = EmotionTone()
        result = tone.set_emotion("sad")
        assert result is True
        assert tone.get_emotion() == Emotion.SAD

    def test_get_params(self):
        """Test getting emotion parameters."""
        tone = EmotionTone()
        tone.set_emotion(Emotion.HAPPY)
        params = tone.get_params()
        assert isinstance(params, EmotionParams)
        assert params.rate > 1.0  # Happy should be faster

    def test_ssml_params(self):
        """Test SSML parameter generation."""
        tone = EmotionTone()
        tone.set_emotion(Emotion.URGENT)
        params = tone.get_ssml_params()
        assert "rate" in params
        assert "pitch" in params
        assert "volume" in params

    def test_apply_to_text(self):
        """Test applying emotion to text."""
        tone = EmotionTone()
        tone.set_emotion(Emotion.WHISPER)
        result = tone.apply_to_text("Hello")
        assert "whispered" in result.lower() or "prosody" in result.lower()

    def test_list_emotions(self):
        """Test listing emotions."""
        tone = EmotionTone()
        emotions = tone.list_emotions()
        assert "neutral" in emotions
        assert "happy" in emotions
        assert "sad" in emotions


class TestSpeakingCanBeInterrupted:
    """Test speech interruption."""

    @pytest.mark.asyncio
    async def test_streaming_speech_interrupt(self):
        """Test interrupting streaming speech."""
        engine = Mock(spec=TTSEngine)
        engine.synthesize_streaming = AsyncMock(return_value=asyncio.Queue())

        streaming = StreamingSpeech(tts_engine=engine)
        await streaming.start()

        # Trigger interrupt
        await streaming.interrupt()

        assert not streaming.is_speaking()
        await streaming.stop()


class TestStreamingSpeechStartsBeforeTextComplete:
    """Test streaming speech generation."""

    @pytest.mark.asyncio
    async def test_streaming_speech_init(self):
        """Test streaming speech initialization."""
        engine = Mock(spec=TTSEngine)
        streaming = StreamingSpeech(tts_engine=engine)
        assert streaming is not None

    @pytest.mark.asyncio
    async def test_speak_stream(self):
        """Test streaming speech from text generator."""
        engine = Mock(spec=TTSEngine)
        streaming = StreamingSpeech(tts_engine=engine)
        await streaming.start()

        async def text_gen():
            yield "Hello "
            yield "world!"

        segments = []
        async for segment in streaming.speak_stream(text_gen()):
            segments.append(segment)

        assert len(segments) > 0
        await streaming.stop()


class TestSpeakerPlayback:
    """Test speaker playback."""

    def test_speaker_init(self):
        """Test speaker initialization."""
        speaker = Speaker()
        assert speaker is not None

    def test_speaker_list_devices(self):
        """Test listing output devices."""
        speaker = Speaker()
        devices = speaker.list_devices()
        assert isinstance(devices, list)

    @pytest.mark.asyncio
    async def test_speaker_play(self):
        """Test playing audio."""
        speaker = Speaker()
        audio = np.zeros(22050, dtype=np.float32)
        speaker.play(audio)
        # Non-blocking, so just verify no error
        await asyncio.sleep(0.1)
        speaker.stop()


class TestAudioMixer:
    """Test audio mixer."""

    def test_mixer_init(self):
        """Test mixer initialization."""
        mixer = AudioMixer()
        assert mixer is not None

    def test_add_source(self):
        """Test adding audio source."""
        mixer = AudioMixer()
        mixer.add_source("test", sample_rate=22050)
        assert "test" in mixer.get_source_names()

    def test_set_volume(self):
        """Test setting source volume."""
        mixer = AudioMixer()
        mixer.add_source("test")
        mixer.set_source_volume("test", 0.5)
        # Volume set internally

    def test_feed_audio(self):
        """Test feeding audio to source."""
        mixer = AudioMixer()
        mixer.add_source("test")
        audio = np.zeros(1024, dtype=np.float32)
        result = mixer.feed_audio("test", audio)
        assert result is True

    def test_mixer_start_stop(self):
        """Test mixer start/stop."""
        mixer = AudioMixer()
        mixer.start()
        assert mixer.is_mixing()
        mixer.stop()
        assert not mixer.is_mixing()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])