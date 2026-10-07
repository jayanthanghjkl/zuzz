"""Communication Hub - escalation chain: web -> telegram -> email."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from brain.communication_hub.exceptions import EscalationError

logger = logging.getLogger(__name__)


class EscalationChain:
    """Escalate messages through channels until acknowledged."""

    def __init__(
        self,
        telegram_bot: Any = None,
        email_sender: Any = None,
        push_pusher: Any = None,
    ) -> None:
        self._telegram = telegram_bot
        self._email = email_sender
        self._push = push_pusher
        self._history: list = []

    async def escalate(self, message: str, start_channel: str = "web") -> Dict[str, Any]:
        """Send a message through the escalation chain.
        
        Args:
            message: The message to send.
            start_channel: Starting channel ('web', 'telegram', 'email', 'push').
        
        Returns:
            Result dict with channel used and status.
        """
        channels = {
            "web": self._send_web,
            "telegram": self._send_telegram,
            "email": self._send_email,
            "push": self._send_push,
        }
        tried = []
        current = start_channel
        while current:
            tried.append(current)
            try:
                result = await channels[current](message)
                self._history.append({"channel": current, "status": "sent", "message": message})
                return {"channel": current, "status": "sent", "tried": tried, **result}
            except Exception as exc:
                logger.warning("escalation failed on %s: %s", current, exc)
                current = self._next_channel(current)
        raise EscalationError(f"all channels failed: {tried}")

    def _next_channel(self, current: str) -> Optional[str]:
        order = ["web", "telegram", "push", "email"]
        try:
            idx = order.index(current)
            return order[idx + 1] if idx + 1 < len(order) else None
        except ValueError:
            return None

    async def _send_web(self, message: str) -> Dict[str, Any]:
        return {"message": message, "channel": "web"}

    async def _send_telegram(self, message: str) -> Dict[str, Any]:
        if self._telegram is None:
            raise EscalationError("telegram not available")
        # Default chat id from env
        import os
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not chat_id:
            raise EscalationError("no telegram chat id")
        return await self._telegram.send_message(chat_id, message)

    async def _send_email(self, message: str) -> Dict[str, Any]:
        if self._email is None:
            raise EscalationError("email not available")
        return self._email.send("ZUZZ Escalation", message)

    async def _send_push(self, message: str) -> Dict[str, Any]:
        if self._push is None:
            raise EscalationError("push not available")
        return self._push.send("ZUZZ", message)
