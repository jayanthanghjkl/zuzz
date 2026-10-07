"""Noise detection and filtering for audio preprocessing."""

import numpy as np
import logging
from typing import Optional
from scipy import signal

logger = logging.getLogger(__name__)


class NoiseDetector:
    """Detects and filters background noise from audio."""

    def __init__(
        self,
        sample_rate: int = 16000,
        noise_threshold: float = 0.005,
        spectral_gate_threshold: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.noise_threshold = noise_threshold
        self.spectral_gate_threshold = spectral_gate_threshold
        self._noise_profile: Optional[np.ndarray] = None
        self._noise_learned = False

    def learn_noise_profile(self, noise_audio: np.ndarray) -> None:
        """Learn noise profile from silence/noise-only audio."""
        if noise_audio.dtype == np.int16:
            noise_audio = noise_audio.astype(np.float32) / 32768.0

        freqs, times, Sxx = signal.spectrogram(noise_audio, fs=self.sample_rate)
        self._noise_profile = np.mean(Sxx, axis=1)
        self._noise_learned = True
        logger.info("Noise profile learned")

    def estimate_noise_level(self, audio: np.ndarray) -> float:
        """Estimate current noise level."""
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        return float(np.mean(audio ** 2))

    def is_noisy(self, audio: np.ndarray) -> bool:
        """Check if audio contains significant noise."""
        return self.estimate_noise_level(audio) > self.noise_threshold

    def reduce_noise(self, audio: np.ndarray) -> np.ndarray:
        """Reduce noise using spectral gating."""
        if not self._noise_learned or self._noise_profile is None:
            return self._simple_noise_gate(audio)

        if audio.dtype == np.int16:
            audio_float = audio.astype(np.float32) / 32768.0
        else:
            audio_float = audio.copy()

        freqs, times, Sxx = signal.spectrogram(audio_float, fs=self.sample_rate)
        noise_gate = self._noise_profile[:, np.newaxis] * self.spectral_gate_threshold
        mask = Sxx > noise_gate
        Sxx_clean = Sxx * mask

        _, audio_clean = signal.istft(Sxx_clean, fs=self.sample_rate)

        if audio.dtype == np.int16:
            audio_clean = (audio_clean * 32768).astype(np.int16)

        return audio_clean

    def _simple_noise_gate(self, audio: np.ndarray) -> np.ndarray:
        """Simple noise gate fallback."""
        if audio.dtype == np.int16:
            audio_float = audio.astype(np.float32) / 32768.0
        else:
            audio_float = audio.copy()

        rms = np.sqrt(np.mean(audio_float ** 2))
        if rms < self.noise_threshold:
            return np.zeros_like(audio)

        threshold = self.noise_threshold * 2
        gated = np.where(np.abs(audio_float) > threshold, audio_float, 0)

        if audio.dtype == np.int16:
            return (gated * 32768).astype(np.int16)
        return gated

    def filter_audio(self, audio: np.ndarray) -> np.ndarray:
        """Filter noise from audio (learn profile if needed)."""
        if not self._noise_learned:
            self.learn_noise_profile(audio)
        return self.reduce_noise(audio)

    def has_noise_profile(self) -> bool:
        """Check if noise profile is learned."""
        return self._noise_learned

    def reset_noise_profile(self) -> None:
        """Reset learned noise profile."""
        self._noise_profile = None
        self._noise_learned = False
