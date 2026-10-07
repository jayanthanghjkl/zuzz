"""Interrupt handler for user interruptions."""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class InterruptType(Enum):
    """Types of interruptions."""
    WAKE_WORD = "wake_word"
    SPEECH_START = "speech_start"
    BUTTON_PRESS = "button_press"
    EXTERNAL_SIGNAL = "external_signal"
    ERROR = "error"


@dataclass
class Interrupt:
    """Interrupt event."""
    type: InterruptType
    source: str
    timestamp: float
    data: Dict = None


class InterruptHandler:
    """Handles interruptions during speech or processing."""

    def __init__(self):
        self._callbacks: Dict[InterruptType, list] = {
            interrupt_type: [] for interrupt_type in InterruptType
        }
        self._general_callbacks: list = []
        self._interrupt_history: list = []
        self._is_interrupted = False
        self._current_task: Optional[asyncio.Task] = None

    def register_callback(
        self,
        interrupt_type: InterruptType,
        callback: Callable[[Interrupt], None],
    ) -> None:
        """Register callback for interrupt type."""
        self._callbacks[interrupt_type].append(callback)

    def register_general_callback(self, callback: Callable[[Interrupt], None]) -> None:
        """Register callback for all interrupts."""
        self._general_callbacks.append(callback)

    def trigger(
        self,
        interrupt_type: InterruptType,
        source: str,
        data: Optional[Dict] = None,
    ) -> Interrupt:
        """Trigger an interrupt."""
        import time
        interrupt = Interrupt(
            type=interrupt_type,
            source=source,
            timestamp=time.time(),
            data=data or {},
        )

        self._interrupt_history.append(interrupt)
        self._is_interrupted = True

        for callback in self._callbacks[interrupt_type]:
            try:
                callback(interrupt)
            except Exception as e:
                logger.error(f"Interrupt callback error: {e}")

        for callback in self._general_callbacks:
            try:
                callback(interrupt)
            except Exception as e:
                logger.error(f"General interrupt callback error: {e}")

        logger.info(f"Interrupt triggered: {interrupt_type.value} from {source}")
        return interrupt

    def handle_wake_word(self, keyword_index: int) -> Interrupt:
        """Handle wake word detection."""
        return self.trigger(
            InterruptType.WAKE_WORD,
            "porcupine",
            {"keyword_index": keyword_index},
        )

    def handle_speech_start(self, audio_level: float) -> Interrupt:
        """Handle speech start detection."""
        return self.trigger(
            InterruptType.SPEECH_START,
            "vad",
            {"audio_level": audio_level},
        )

    def handle_button_press(self, button_id: str) -> Interrupt:
        """Handle button press."""
        return self.trigger(
            InterruptType.BUTTON_PRESS,
            "button",
            {"button_id": button_id},
        )

    def clear_interrupt(self) -> None:
        """Clear interrupt state."""
        self._is_interrupted = False

    def is_interrupted(self) -> bool:
        """Check if interrupted."""
        return self._is_interrupted

    def get_history(self, limit: int = 10) -> list:
        """Get interrupt history."""
        return self._interrupt_history[-limit:]

    def interrupt_current_task(self) -> bool:
        """Interrupt current async task."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self._current_task = None
            return True
        return False

    def set_current_task(self, task: asyncio.Task) -> None:
        """Set current task for interruption."""
        self._current_task = task

    def get_status(self) -> Dict:
        """Get interrupt status."""
        return {
            "interrupted": self._is_interrupted,
            "recent_interrupts": len(self._interrupt_history),
            "last_interrupt": self._interrupt_history[-1].type.value if self._interrupt_history else None,
        }


class InterruptError(Exception):
    """Interrupt handler error."""
    pass
