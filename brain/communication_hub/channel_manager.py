"""Communication Hub - route to the right channel."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from brain.communication_hub.exceptions import CommunicationError

logger = logging.getLogger(__name__)


class ChannelManager:
    """Select and use the appropriate notification channel."""

    def __init__(
        self,
        telegram_bot: Any = None,
        email_sender: Any = None,
        push_pusher: Any = None,
    ) -> None:
        self._telegram = telegram_bot
        self._email = email_sender
        self._push = push_pusher

    def get_status(self) -> Dict[str, Any]:
        """Return the status of each channel.
        
        Returns:
            Dict mapping channel names to configured booleans.
        """
        return {
            "telegram": self._telegram.is_configured() if self._telegram else False,
            "email": self._email.is_configured() if self._email else False,
            "push": self._push.is_configured() if self._push else False,
        }

    async def send(self, message: str, channel: str = "auto", priority: str = "normal", **kwargs: Any) -> Dict[str, Any]:
        """Send a message via the requested or best available channel.
        
        Args:
            message: The message text.
            channel: 'telegram', 'email', 'push', or 'auto'.
            priority: Message priority.
            **kwargs: Extra args passed to the channel sender.
        
        Returns:
            Result dict.
        
        Raises:
            CommunicationError: If no channel is available.
        """
        if channel == "auto":
            channel = self._best_channel()
        sender = {
            "telegram": self._send_telegram,
            "email": self._send_email,
            "push": self._send_push,
        }.get(channel)
        if sender is None:
            raise CommunicationError(f"unknown channel: {channel}")
        try:
            return await sender(message, priority=priority, **kwargs)
        except CommunicationError:
            raise
        except Exception as exc:
            raise CommunicationError(f"channel {channel} failed: {exc}") from exc

    def _best_channel(self) -> str:
        status = self.get_status()
        for ch in ("telegram", "push", "email"):
            if status.get(ch):
                return ch
        raise CommunicationError("no notification channel configured")

    async def _send_telegram(self, message: str, priority: str = "normal", **kwargs: Any) -> Dict[str, Any]:
        if self._telegram is None or not self._telegram.is_configured():
            raise CommunicationError("telegram not configured")
        chat_id = kwargs.get("chat_id") or kwargs.get("to") or ""
        buttons = kwargs.get("buttons")
        return await self._telegram.send_message(chat_id, message, buttons=buttons)

    async def _send_email(self, message: str, priority: str = "normal", **kwargs: Any) -> Dict[str, Any]:
        if self._email is None or not self._email.is_configured():
            raise CommunicationError("email not configured")
        subject = kwargs.get("subject", "ZUZZ Notification")
        to = kwargs.get("to")
        return self._email.send(subject, message, to=to)

    async def _send_push(self, message: str, priority: str = "normal", **kwargs: Any) -> Dict[str, Any]:
        if self._push is None or not self._push.is_configured():
            raise CommunicationError("push not configured")
        title = kwargs.get("title", "ZUZZ")
        return self._push.send(title, message, priority=priority)
