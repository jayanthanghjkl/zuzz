"""Idle detector for sleep mode."""

import asyncio
import logging
import time
from typing import Optional, Callable, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IdleConfig:
    """Idle detection configuration."""
    idle_threshold: float = 60.0
    sleep_threshold: float = 300.0
    check_interval: float = 10.0


class IdleDetector:
    """Detects user inactivity and manages sleep mode."""

    def __init__(self, config: Optional[IdleConfig] = None):
        self.config = config or IdleConfig()
        self._last_activity = time.time()
        self._is_sleeping = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._on_idle: Optional[Callable[[], None]] = None
        self._on_sleep: Optional[Callable[[], None]] = None
        self._on_wake: Optional[Callable[[], None]] = None

    def set_callbacks(
        self,
        on_idle: Optional[Callable[[], None]] = None,
        on_sleep: Optional[Callable[[], None]] = None,
        on_wake: Optional[Callable[[], None]] = None,
    ) -> None:
        """Set idle/sleep/wake callbacks."""
        self._on_idle = on_idle
        self._on_sleep = on_sleep
        self._on_wake = on_wake

    def activity(self) -> None:
        """Record user activity."""
        was_sleeping = self._is_sleeping
        self._last_activity = time.time()

        if was_sleeping:
            self._is_sleeping = False
            logger.info("Wake from sleep")
            if self._on_wake:
                self._on_wake()

    async def start(self) -> None:
        """Start idle monitoring."""
        if self._monitor_task:
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Idle detector started")

    async def stop(self) -> None:
        """Stop idle monitoring."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Idle detector stopped")

    async def _monitor_loop(self) -> None:
        """Monitor idle state."""
        idle_notified = False
        sleep_notified = False

        while True:
            await asyncio.sleep(self.config.check_interval)
            idle_duration = time.time() - self._last_activity

            if idle_duration >= self.config.sleep_threshold and not sleep_notified:
                self._is_sleeping = True
                sleep_notified = True
                idle_notified = True
                logger.info("Entering sleep mode")
                if self._on_sleep:
                    self._on_sleep()

            elif idle_duration >= self.config.idle_threshold and not idle_notified:
                idle_notified = True
                logger.info("User idle detected")
                if self._on_idle:
                    self._on_idle()

            elif idle_duration < self.config.idle_threshold:
                idle_notified = False
                sleep_notified = False

    def is_idle(self) -> bool:
        """Check if user is idle."""
        return time.time() - self._last_activity >= self.config.idle_threshold

    def is_sleeping(self) -> bool:
        """Check if in sleep mode."""
        return self._is_sleeping

    def get_idle_duration(self) -> float:
        """Get idle duration in seconds."""
        return time.time() - self._last_activity

    def get_status(self) -> Dict:
        """Get idle status."""
        return {
            "idle_duration": self.get_idle_duration(),
            "is_idle": self.is_idle(),
            "is_sleeping": self._is_sleeping,
            "idle_threshold": self.config.idle_threshold,
            "sleep_threshold": self.config.sleep_threshold,
        }


class IdleError(Exception):
    """Idle detector error."""
    pass
