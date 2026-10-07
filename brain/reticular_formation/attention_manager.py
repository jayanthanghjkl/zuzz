"""Attention manager for focus tracking."""

import logging
import time
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)


class FocusState(Enum):
    """Focus states."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    FOCUSED = "focused"


@dataclass
class FocusEvent:
    """Focus change event."""
    state: FocusState
    timestamp: float
    metadata: Dict = field(default_factory=dict)


class AttentionManager:
    """Manages attention and focus states."""

    def __init__(
        self,
        focus_timeout: float = 30.0,
        idle_threshold: float = 60.0,
    ):
        self.focus_timeout = focus_timeout
        self.idle_threshold = idle_threshold
        self._current_state = FocusState.IDLE
        self._state_history: deque = deque(maxlen=100)
        self._focus_target: Optional[str] = None
        self._callbacks: List[Callable[[FocusState, FocusState], None]] = []
        self._last_activity = time.time()

    def add_callback(self, callback: Callable[[FocusState, FocusState], None]) -> None:
        """Add focus change callback."""
        self._callbacks.append(callback)

    def set_state(self, state: FocusState, target: Optional[str] = None, metadata: Dict = None) -> None:
        """Set focus state."""
        old_state = self._current_state
        self._current_state = state
        self._focus_target = target
        self._last_activity = time.time()

        event = FocusEvent(
            state=state,
            timestamp=self._last_activity,
            metadata=metadata or {},
        )
        self._state_history.append(event)

        if old_state != state:
            logger.info(f"Focus changed: {old_state.value} -> {state.value}")
            for callback in self._callbacks:
                try:
                    callback(old_state, state)
                except Exception as e:
                    logger.error(f"Focus callback error: {e}")

    def get_state(self) -> FocusState:
        """Get current focus state."""
        return self._current_state

    def get_target(self) -> Optional[str]:
        """Get current focus target."""
        return self._focus_target

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self._last_activity = time.time()

    def is_idle(self) -> bool:
        """Check if idle."""
        return time.time() - self._last_activity > self.idle_threshold

    def is_focused(self) -> bool:
        """Check if focused on a task."""
        return self._current_state in (FocusState.FOCUSED, FocusState.PROCESSING)

    def get_idle_duration(self) -> float:
        """Get idle duration in seconds."""
        return time.time() - self._last_activity

    def get_focus_duration(self) -> float:
        """Get current focus duration."""
        if self._state_history:
            return time.time() - self._state_history[-1].timestamp
        return 0.0

    def get_history(self, limit: int = 10) -> List[FocusEvent]:
        """Get focus history."""
        return list(self._state_history)[-limit:]

    def should_timeout(self) -> bool:
        """Check if focus should timeout."""
        if self._current_state in (FocusState.LISTENING, FocusState.PROCESSING):
            return self.get_focus_duration() > self.focus_timeout
        return False

    def reset(self) -> None:
        """Reset to idle state."""
        self.set_state(FocusState.IDLE)

    def get_status(self) -> Dict:
        """Get attention status."""
        return {
            "state": self._current_state.value,
            "target": self._focus_target,
            "idle_duration": self.get_idle_duration(),
            "focus_duration": self.get_focus_duration(),
            "is_idle": self.is_idle(),
            "is_focused": self.is_focused(),
        }


class AttentionError(Exception):
    """Attention manager error."""
    pass
