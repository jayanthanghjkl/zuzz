"""Reticular Formation - Main orchestrator for attention and wake word."""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from .wake_word_detector import WakeWordDetector
from .attention_manager import AttentionManager, FocusState
from .idle_detector import IdleDetector, IdleConfig
from .interrupt_handler import InterruptHandler, InterruptType
from .exceptions import ReticularFormationError

logger = logging.getLogger(__name__)


@dataclass
class ReticularConfig:
    """Configuration for Reticular Formation."""
    wake_word_access_key: str = ""
    wake_word_keywords: list = None
    wake_word_sensitivities: list = None
    focus_timeout: float = 30.0
    idle_threshold: float = 60.0
    sleep_threshold: float = 300.0


class ReticularFormation:
    """Orchestrator for attention, wake word, and interrupt handling."""

    def __init__(self, config: Optional[ReticularConfig] = None):
        self.config = config or ReticularConfig()
        if self.config.wake_word_keywords is None:
            self.config.wake_word_keywords = ["hey zuzz", "okay zuzz"]

        self.wake_word = None
        self.attention = AttentionManager(
            focus_timeout=self.config.focus_timeout,
            idle_threshold=self.config.idle_threshold,
        )
        self.idle = IdleDetector(IdleConfig(
            idle_threshold=self.config.idle_threshold,
            sleep_threshold=self.config.sleep_threshold,
        ))
        self.interrupts = InterruptHandler()

        self._is_awake = False
        self._wake_callback: Optional[Callable[[], None]] = None
        self._sleep_callback: Optional[Callable[[], None]] = None

    async def initialize(self) -> None:
        """Initialize components."""
        if self.config.wake_word_access_key:
            self.wake_word = WakeWordDetector(
                access_key=self.config.wake_word_access_key,
                keywords=self.config.wake_word_keywords,
                sensitivities=self.config.wake_word_sensitivities,
            )
            self.wake_word.set_callback(self._on_wake_word)

        self.idle.set_callbacks(
            on_idle=self._on_idle,
            on_sleep=self._on_sleep,
            on_wake=self._on_wake,
        )

        self.attention.add_callback(self._on_focus_change)
        self.interrupts.register_general_callback(self._on_interrupt)

        await self.idle.start()
        logger.info("Reticular Formation initialized")

    async def start_wake_word_detection(self, decision: dict) -> None:
        """Start wake word detection."""
        if self.wake_word and not self.wake_word.is_listening():
            await self.wake_word.start()
            self._is_awake = True
            logger.info("Wake word detection started")

    async def stop_wake_word_detection(self, decision: dict) -> None:
        """Stop wake word detection."""
        if self.wake_word and self.wake_word.is_listening():
            await self.wake_word.stop()
            logger.info("Wake word detection stopped")

    def is_awake(self) -> bool:
        """Check if system is awake."""
        return self._is_awake and not self.idle.is_sleeping()

    def on_wake_word_detected(self, callback: Callable[[], None]) -> None:
        """Register wake word callback."""
        self._wake_callback = callback

    def on_sleep(self, callback: Callable[[], None]) -> None:
        """Register sleep callback."""
        self._sleep_callback = callback

    def _on_wake_word(self, keyword_index: int) -> None:
        """Handle wake word detection."""
        self.idle.activity()
        self.interrupts.handle_wake_word(keyword_index)
        self.attention.set_state(FocusState.LISTENING, "wake_word")
        if self._wake_callback:
            self._wake_callback()
        logger.info(f"Wake word detected (index: {keyword_index})")

    def _on_idle(self) -> None:
        """Handle idle detection."""
        self.attention.set_state(FocusState.IDLE)
        logger.info("Idle detected")

    def _on_sleep(self) -> None:
        """Handle sleep mode."""
        self._is_awake = False
        self.attention.set_state(FocusState.IDLE)
        if self._sleep_callback:
            self._sleep_callback()
        logger.info("Sleep mode activated")

    def _on_wake(self) -> None:
        """Handle wake from sleep."""
        self._is_awake = True
        logger.info("Woke from sleep")

    def _on_focus_change(self, old_state: FocusState, new_state: FocusState) -> None:
        """Handle focus change."""
        logger.debug(f"Focus: {old_state.value} -> {new_state.value}")

    def _on_interrupt(self, interrupt) -> None:
        """Handle interrupt."""
        self.idle.activity()
        if interrupt.type == InterruptType.WAKE_WORD:
            self.attention.set_state(FocusState.LISTENING, "interrupt")
        elif interrupt.type == InterruptType.SPEECH_START:
            self.attention.set_state(FocusState.LISTENING, "speech")

    def record_activity(self) -> None:
        """Record user activity."""
        self.idle.activity()
        self.attention.update_activity()

    def get_status(self) -> Dict:
        """Get overall status."""
        return {
            "awake": self.is_awake(),
            "attention": self.attention.get_status(),
            "idle": self.idle.get_status(),
            "interrupts": self.interrupts.get_status(),
            "wake_word_active": self.wake_word.is_listening() if self.wake_word else False,
        }

    async def shutdown(self) -> None:
        """Shutdown all components."""
        if self.wake_word:
            await self.wake_word.stop()
        await self.idle.stop()
        logger.info("Reticular Formation shutdown")
