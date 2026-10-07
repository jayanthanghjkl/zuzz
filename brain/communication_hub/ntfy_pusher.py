"""Communication Hub - ntfy push notifications."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import ntfy

from brain.communication_hub.exceptions import PushError

logger = logging.getLogger(__name__)


class NtfyPusher:
    """Send push notifications via ntfy."""

    def __init__(self) -> None:
        self._topic = os.getenv("NTFY_TOPIC", "")
        self._default_topic = self._topic

    def is_configured(self) -> bool:
        """Return True when an ntfy topic is available."""
        return bool(self._default_topic)

    def send(self, title: str, message: str, priority: str = "normal", topic: Optional[str] = None) -> Dict[str, Any]:
        """Send a push notification.
        
        Args:
            title: Notification title.
            message: Notification message.
            priority: Priority level (low, normal, high, urgent).
            topic: Optional topic override.
        
        Returns:
            Result dict.
        
        Raises:
            PushError: On send failure.
        """
        if not self.is_configured():
            raise PushError("ntfy not configured")
        target = topic or self._default_topic
        try:
            response = ntfy.notify(
                target,
                message,
                title=title,
                priority=priority,
            )
            logger.info("sent ntfy notification to %s: %s", target, title)
            return {"sent": True, "topic": target, "response": str(response)}
        except Exception as exc:
            raise PushError(f"failed to send ntfy notification: {exc}") from exc
