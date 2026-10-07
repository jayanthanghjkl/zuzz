"""Background Agent Template: Email Monitor.

Checks Gmail every 15 min via IMAP, summarizes important emails,
and sends a daily briefing.
"""

from __future__ import annotations

import imaplib
import email
from email.header import decode_header
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class EmailMonitorAgent:
    """Monitor Gmail inbox for important emails."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._imap_host = config.get("imap_host", "imap.gmail.com")
        self._username = config.get("username", os.getenv("EMAIL_USER", ""))
        self._password = config.get("password", os.getenv("EMAIL_PASSWORD", ""))
        self._important_senders = config.get("important_senders", [])

    def check_inbox(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Check the inbox for new emails.
        
        Args:
            limit: Max emails to fetch.
        
        Returns:
            List of email summaries.
        """
        if not self._username or not self._password:
            logger.warning("email credentials not configured")
            return []
        try:
            mail = imaplib.IMAP4_SSL(self._imap_host)
            mail.login(self._username, self._password)
            mail.select("inbox")
            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()[-limit:]
            results = []
            for mid in ids:
                _, msg_data = mail.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject, _ = decode_header(msg.get("Subject", ""))[0]
                subject = subject.decode() if isinstance(subject, bytes) else str(subject)
                sender = msg.get("From", "")
                results.append({"subject": subject, "sender": sender, "id": mid.decode()})
            mail.logout()
            return results
        except Exception as exc:
            logger.error("email check failed: %s", exc)
            return []

    def summarize(self, emails: List[Dict[str, Any]]) -> str:
        """Summarize emails into a briefing string."""
        if not emails:
            return "No new important emails."
        lines = [f"{e['subject']} from {e['sender']}" for e in emails[:5]]
        return "\n".join(lines)

    def run(self) -> Dict[str, Any]:
        """Run the monitoring cycle."""
        emails = self.check_inbox()
        summary = self.summarize(emails)
        return {
            "agent_type": "email_monitor",
            "timestamp": datetime.utcnow().isoformat(),
            "new_emails": len(emails),
            "summary": summary,
        }
