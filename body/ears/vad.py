"""Voice Activity Detection (VAD) for speech vs silence detection."""

import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """Detects voice activity in audio streams using energy-based VAD."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration: float = 0.03,
        energy_threshold: float = 0.01,
        silence_duration: float = 0.5,
        speech_pad: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.frame_duration = frame_duration
        self.frame_size = int(sample_rate * frame_duration)
        self.energy_threshold = energy_threshold
        self.silence_frames = int(silence_duration / frame_duration)
        self.speech_pad_frames = int(speech_pad / frame_duration)
        self._silence_counter = 0
        self._speech_counter = 0
        self._in_speech = False
        self._speech_buffer = []

    def process_frame(self, frame: np.ndarray) -> bool:
        """
        Process a single audio frame and return True if speech is detected.
        Frame should be of size frame_size.
        """
        if len(frame) != self.frame_size:
            frame = self._resize_frame(frame)

        energy = self._calculate_energy(frame)
        is_speech = energy > self.energy_threshold

        if is_speech:
            self._silence_counter = 0
            self._speech_counter += 1
            if not self._in_speech and self._speech_counter >= self.speech_pad_frames:
                self._in_speech = True
        else:
            self._speech_counter = 0
            if self._in_speech:
                self._silence_counter += 1
                if self._silence_counter >= self.silence_frames:
                    self._in_speech = False
                    self._silence_counter = 0

        return self._in_speech

    def _calculate_energy(self, frame: np.ndarray) -> float:
        """Calculate normalized energy of frame."""
        if frame.dtype == np.int16:
            frame = frame.astype(np.float32) / 32768.0
        elif frame.dtype == np.int32:
            frame = frame.astype(np.float32) / 2147483648.0
        return float(np.mean(frame ** 2))

    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        """Resize frame to expected size."""
        if len(frame) > self.frame_size:
            return frame[:self.frame_size]
        else:
            padded = np.zeros(self.frame_size, dtype=frame.dtype)
            padded[:len(frame)] = frame
            return padded

    def process_audio(self, audio: np.ndarray) -> list:
        """
        Process full audio array and return list of (start, end) speech segments
        in samples.
        """
        segments = []
        in_segment = False
        segment_start = 0

        for i in range(0, len(audio), self.frame_size):
            frame = audio[i:i + self.frame_size]
            is_speech = self.process_frame(frame)

            if is_speech and not in_segment:
                in_segment = True
                segment_start = max(0, i - self.speech_pad_frames * self.frame_size)
            elif not is_speech and in_segment:
                in_segment = False
                segment_end = min(len(audio), i + self.speech_pad_frames * self.frame_size)
                segments.append((segment_start, segment_end))

        if in_segment:
            segments.append((segment_start, len(audio)))

        return segments

    def reset(self) -> None:
        """Reset VAD state."""
        self._silence_counter = 0
        self._speech_counter = 0
        self._in_speech = False
        self._speech_buffer = []

    def is_speech(self) -> bool:
        """Check if currently in speech segment."""
        return self._in_speech