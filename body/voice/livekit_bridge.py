"""LiveKit bridge for real-time WebRTC voice communication."""

import asyncio
import logging
import numpy as np
from typing import Optional, Callable, AsyncGenerator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from livekit import rtc
    from livekit.agents import (
        AutoSubscribe,
        JobContext,
        JobProcess,
        WorkerOptions,
        cli,
        llm,
        voice_assistant,
    )
    from livekit.plugins import openai, silero
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False
    logger.warning("LiveKit not available. Install livekit-agents for WebRTC support.")

from ..ears.audio_stream import AudioStream
from ..ears.microphone import Microphone
from .speaker import Speaker


@dataclass
class LiveKitConfig:
    """Configuration for LiveKit connection."""
    url: str
    token: str
    room_name: str = "zuzz-voice"
    sample_rate: int = 48000
    channels: int = 1


class LiveKitBridge:
    """Bridge for real-time voice communication via LiveKit WebRTC."""

    def __init__(self, config: LiveKitConfig):
        if not LIVEKIT_AVAILABLE:
            raise LiveKitError("LiveKit not installed. Run: pip install livekit-agents")
        self.config = config
        self._room: Optional[rtc.Room] = None
        self._audio_source: Optional[rtc.AudioSource] = None
        self._audio_track: Optional[rtc.LocalAudioTrack] = None
        self._microphone = Microphone(sample_rate=config.sample_rate)
        self._speaker = Speaker(sample_rate=config.sample_rate)
        self._is_connected = False
        self._on_audio_received: Optional[Callable[[bytes], None]] = None
        self._on_participant_joined: Optional[Callable[[str], None]] = None
        self._on_participant_left: Optional[Callable[[str], None]] = None

    async def connect(self) -> None:
        """Connect to LiveKit room."""
        if self._is_connected:
            return

        self._room = rtc.Room()
        self._setup_room_handlers()

        await self._room.connect(self.config.url, self.config.token)
        self._is_connected = True

        self._audio_source = rtc.AudioSource(self.config.sample_rate, self.config.channels)
        self._audio_track = rtc.LocalAudioTrack.create_audio_track("zuzz-mic", self._audio_source)
        await self._room.local_participant.publish_track(self._audio_track)

        self._microphone.set_callback(self._on_mic_data)
        self._microphone.start()

        logger.info(f"Connected to LiveKit room: {self.config.room_name}")

    async def disconnect(self) -> None:
        """Disconnect from LiveKit room."""
        if not self._is_connected:
            return

        self._microphone.stop()
        if self._audio_track:
            await self._room.local_participant.unpublish_track(self._audio_track.sid)
        await self._room.disconnect()
        self._is_connected = False
        logger.info("Disconnected from LiveKit room")

    def _setup_room_handlers(self) -> None:
        """Set up room event handlers."""
        @self._room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant):
            logger.info(f"Participant joined: {participant.identity}")
            if self._on_participant_joined:
                self._on_participant_joined(participant.identity)

            @participant.on("track_subscribed")
            def on_track_subscribed(track: rtc.RemoteTrack, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                if track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._handle_audio_track(track, participant.identity)

        @self._room.on("participant_disconnected")
        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            logger.info(f"Participant left: {participant.identity}")
            if self._on_participant_left:
                self._on_participant_left(participant.identity)

    def _handle_audio_track(self, track: rtc.RemoteAudioTrack, participant_id: str) -> None:
        """Handle incoming audio track from participant."""
        audio_stream = rtc.AudioStream(track)

        async def process_audio():
            async for frame in audio_stream:
                if self._on_audio_received:
                    self._on_audio_received(frame.data.tobytes(), participant_id)

        asyncio.create_task(process_audio())

    def _on_mic_data(self, data: np.ndarray) -> None:
        """Callback for microphone data."""
        if self._audio_source and self._is_connected:
            frame = rtc.AudioFrame(
                data=data.tobytes(),
                sample_rate=self.config.sample_rate,
                num_channels=self.config.channels,
                samples_per_channel=len(data) // self.config.channels,
            )
            asyncio.create_task(self._audio_source.capture_frame(frame))

    def set_audio_received_callback(self, callback: Callable[[bytes, str], None]) -> None:
        """Set callback for received audio data."""
        self._on_audio_received = callback

    def set_participant_callbacks(
        self,
        on_joined: Optional[Callable[[str], None]] = None,
        on_left: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Set participant join/leave callbacks."""
        self._on_participant_joined = on_joined
        self._on_participant_left = on_left

    def play_audio(self, audio_data: bytes, sample_rate: int = None) -> None:
        """Play audio through speaker."""
        sr = sample_rate or self.config.sample_rate
        if sr != self._speaker.sample_rate:
            import scipy.signal as signal
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            audio_array = signal.resample(audio_array, int(len(audio_array) * self._speaker.sample_rate / sr))
            audio_data = (audio_array * 32768).astype(np.int16).tobytes()
        self._speaker.play(np.frombuffer(audio_data, dtype=np.int16))

    def is_connected(self) -> bool:
        """Check if connected to LiveKit."""
        return self._is_connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


class LiveKitError(Exception):
    """LiveKit bridge error."""
    pass