"""Communication Hub - orchestrator for all notification channels."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from brain.communication_hub.channel_manager import ChannelManager
from brain.communication_hub.email_sender import EmailSender
from brain.communication_hub.escalation_chain import EscalationChain
from brain.communication_hub.ntfy_pusher import NtfyPusher
from brain.communication_hub.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


class CommunicationHub:
    """Orchestrate notifications across Telegram, Email, ntfy, and escalation."""

    def __init__(self) -> None:
        self._telegram = TelegramBot()
        self._email = EmailSender()
        self._push = NtfyPusher()
        self._channels = ChannelManager(
            telegram_bot=self._telegram,
            email_sender=self._email,
            push_pusher=self._push,
        )
        self._escalation = EscalationChain(
            telegram_bot=self._telegram,
            email_sender=self._email,
            push_pusher=self._push,
        )

    async def start(self) -> None:
        """Start background services."""
        await self._telegram.start()
        logger.info("communication hub started")

    async def stop(self) -> None:
        """Stop background services."""
        await self._telegram.stop()
        logger.info("communication hub stopped")

    async def send_notification(
        self,
        message: str,
        priority: str = "normal",
        channel: str = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Send a notification.
        
        Args:
            message: The message text.
            priority: Priority level.
            channel: Target channel or 'auto'.
            **kwargs: Channel-specific args.
        
        Returns:
            Result dict.
        """
        return await self._channels.send(message, channel=channel, priority=priority, **kwargs)

    async def send_telegram(self, message: str, buttons: Optional[list] = None, chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a Telegram message.
        
        Args:
            message: Message text.
            buttons: Optional button labels.
            chat_id: Target chat ID.
        
        Returns:
            Result dict.
        """
        return await self._channels.send(message, channel="telegram", buttons=buttons, chat_id=chat_id)

    async def send_email(self, subject: str, body: str, to: Optional[str] = None) -> Dict[str, Any]:
        """Send an email.
        
        Args:
            subject: Email subject.
            body: Email body.
            to: Recipient email.
        
        Returns:
            Result dict.
        """
        return self._email.send(subject, body, to=to)

    async def send_push(self, title: str, message: str, priority: str = "normal") -> Dict[str, Any]:
        """Send a push notification.
        
        Args:
            title: Notification title.
            message: Notification message.
            priority: Priority level.
        
        Returns:
            Result dict.
        """
        return self._push.send(title, message, priority=priority)

    async def request_approval(self, action: str, timeout_seconds: int = 300, chat_id: Optional[str] = None) -> Optional[bool]:
        """Request approval via Telegram.
        
        Args:
            action: Description of the action.
            timeout_seconds: How long to wait.
            chat_id: Target chat ID.
        
        Returns:
            True if approved, False if denied, None if timeout.
        """
        if not chat_id:
            chat_id = ""
        return await self._telegram.request_approval(chat_id, action, timeout_seconds)

    async def escalate(self, message: str, start_channel: str = "web") -> Dict[str, Any]:
        """Escalate a message through channels.
        
        Args:
            message: The message.
            start_channel: Starting channel.
        
        Returns:
            Result dict.
        """
        return await self._escalation.escalate(message, start_channel=start_channel)

    def get_channel_status(self) -> Dict[str, Any]:
        """Return channel configuration status."""
        return self._channels.get_status()
