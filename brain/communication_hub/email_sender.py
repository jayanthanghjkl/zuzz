"""Communication Hub - SMTP email integration."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

from brain.communication_hub.exceptions import EmailError

logger = logging.getLogger(__name__)


class EmailSender:
    """Send emails via SMTP."""

    def __init__(self) -> None:
        self._host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self._port = int(os.getenv("SMTP_PORT", "587"))
        self._user = os.getenv("SMTP_USER", "")
        self._password = os.getenv("SMTP_PASSWORD", "")
        self._from = os.getenv("EMAIL_FROM", self._user)

    def is_configured(self) -> bool:
        """Return True when SMTP credentials are available."""
        return bool(self._host and self._user and self._password)

    def send(self, subject: str, body: str, to: Optional[str] = None) -> Dict[str, Any]:
        """Send an email.
        
        Args:
            subject: Email subject.
            body: Email body.
            to: Recipient email address.
        
        Returns:
            Result dict.
        
        Raises:
            EmailError: On send failure.
        """
        if not self.is_configured():
            raise EmailError("email not configured")
        recipient = to or os.getenv("EMAIL_TO", self._user)
        if not recipient:
            raise EmailError("no recipient configured")
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = recipient
        try:
            with smtplib.SMTP(self._host, self._port) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.send_message(msg)
            logger.info("sent email to %s: %s", recipient, subject)
            return {"sent": True, "to": recipient, "subject": subject}
        except Exception as exc:
            raise EmailError(f"failed to send email: {exc}") from exc
