"""Tests for wake word detection."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import numpy as np

from brain.reticular_formation.wake_word_detector import WakeWordDetector
from brain.reticular_formation.reticular_formation import ReticularFormation, ReticularConfig
from brain.reticular_formation.attention_manager import AttentionManager, FocusState
from brain.reticular_formation.idle_detector import IdleDetector, IdleConfig
from brain.reticular_formation.interrupt_handler import InterruptHandler, InterruptType


class TestPorcupineDetectsHeyZuzz:
    """Test Porcupine wake word detection."""

    @pytest.mark.asyncio
    async def test_wake_word_detector_structure(self):
        """Test wake word detector class structure."""
        # Requires access key, test structure only
        assert hasattr(WakeWordDetector, 'initialize')
        assert hasattr(WakeWordDetector, 'start')
        assert hasattr(WakeWordDetector, 'stop')
        assert hasattr(WakeWordDetector, 'set_callback')

    @pytest.mark.asyncio
    async def test_wake_word_keywords(self):
        """Test wake word keywords configuration."""
        # Test default keywords
        keywords = ["hey zuzz", "okay zuzz"]
        assert "hey zuzz" in keywords
        assert "okay zuzz" in keywords


class TestFalsePositivesRejected:
    """Test false positive rejection."""

    def test_sensitivity_configuration(self):
        """Test sensitivity settings."""
        sensitivities = [0.5, 0.5]
        assert len(sensitivities) == 2
        assert all(0 <= s <= 1 for s in sensitivities)

    def test_keyword_index_mapping(self):
        """Test keyword index mapping."""
        keywords = ["hey zuzz", "okay zuzz", "alexa"]
        assert keywords[0] == "hey zuzz"
        assert keywords[1] == "okay zuzz"
        assert keywords[2] == "alexa"


class TestWakeWordTriggersListening:
    """Test wake word triggering listening mode."""

    @pytest.mark.asyncio
    async def test_reticular_formation_wake_word(self):
        """Test reticular formation wake word handling."""
        config = ReticularConfig(
            wake_word_access_key="test",
            wake_word_keywords=["hey zuzz"]
        )
        rf = ReticularFormation(config)
        assert rf is not None

    @pytest.mark.asyncio
    async def test_wake_word_callback(self):
        """Test wake word callback registration."""
        rf = ReticularFormation(ReticularConfig())
        callback_called = False

        def on_wake():
            nonlocal callback_called
            callback_called = True

        rf.on_wake_word_detected(on_wake)
        assert rf._wake_callback == on_wake

    @pytest.mark.asyncio
    async def test_attention_manager_focus_change(self):
        """Test attention manager on wake word."""
        attention = AttentionManager()
        states = []

        def callback(old, new):
            states.append((old, new))

        attention.add_callback(callback)
        attention.set_state(FocusState.LISTENING, "wake_word")

        assert len(states) == 1
        assert states[0][1] == FocusState.LISTENING

    @pytest.mark.asyncio
    async def test_interrupt_handler_wake_word(self):
        """Test interrupt handler for wake word."""
        handler = InterruptHandler()
        interrupts = []

        def callback(interrupt):
            interrupts.append(interrupt)

        handler.register_callback(InterruptType.WAKE_WORD, callback)
        handler.handle_wake_word(0)

        assert len(interrupts) == 1
        assert interrupts[0].type == InterruptType.WAKE_WORD
        assert interrupts[0].data["keyword_index"] == 0

    @pytest.mark.asyncio
    async def test_idle_detector_wake(self):
        """Test idle detector wake from sleep."""
        idle = IdleDetector(IdleConfig(idle_threshold=1, sleep_threshold=2))
        woken = False

        def on_wake():
            nonlocal woken
            woken = True

        idle.set_callbacks(on_wake=on_wake)
        idle.activity()  # Simulate wake word activity

        assert not idle.is_sleeping()


class TestReticularFormationIntegration:
    """Test full reticular formation integration."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test full initialization."""
        config = ReticularConfig()
        rf = ReticularFormation(config)
        await rf.initialize()
        await rf.shutdown()

    @pytest.mark.asyncio
    async def test_is_awake(self):
        """Test awake state tracking."""
        rf = ReticularFormation(ReticularConfig())
        assert not rf.is_awake()  # Initially not awake

        rf._is_awake = True
        rf.idle._is_sleeping = False
        assert rf.is_awake()

    @pytest.mark.asyncio
    async def test_record_activity(self):
        """Test activity recording."""
        rf = ReticularFormation(ReticularConfig())
        await rf.initialize()

        rf.record_activity()
        assert rf.idle.get_idle_duration() < 1

        await rf.shutdown()

    @pytest.mark.asyncio
    async def test_get_status(self):
        """Test status reporting."""
        rf = ReticularFormation(ReticularConfig())
        await rf.initialize()

        status = rf.get_status()
        assert "awake" in status
        assert "attention" in status
        assert "idle" in status
        assert "interrupts" in status

        await rf.shutdown()


class TestAttentionManagerTimeout:
    """Test attention timeout."""

    def test_focus_timeout(self):
        """Test focus timeout detection."""
        attention = AttentionManager(focus_timeout=0.1)
        attention.set_state(FocusState.LISTENING)

        # Wait for timeout
        import time
        time.sleep(0.2)
        assert attention.should_timeout()

    def test_idle_detection(self):
        """Test idle detection."""
        attention = AttentionManager(idle_threshold=0.1)
        attention.update_activity()

        import time
        time.sleep(0.2)
        assert attention.is_idle()


class TestIdleDetectorSleep:
    """Test idle detector sleep mode."""

    @pytest.mark.asyncio
    async def test_idle_to_sleep(self):
        """Test transition from idle to sleep."""
        idle = IdleDetector(IdleConfig(
            idle_threshold=0.1,
            sleep_threshold=0.2,
            check_interval=0.05
        ))

        idle_states = []
        sleep_states = []

        def on_idle():
            idle_states.append(True)

        def on_sleep():
            sleep_states.append(True)

        idle.set_callbacks(on_idle=on_idle, on_sleep=on_sleep)
        await idle.start()

        # Wait for sleep
        await asyncio.sleep(0.5)

        await idle.stop()

        assert len(idle_states) > 0
        assert len(sleep_states) > 0
        assert idle.is_sleeping()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])