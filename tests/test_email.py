"""Tests for the Email sender integration."""

from __future__ import annotations

import pytest

from brain.communication_hub.email_sender import EmailSender
from brain.communication_hub.exceptions import EmailError


@pytest.fixture
def email_sender():
    return EmailSender()


def test_email_not_configured_without_credentials(monkeypatch):
    """Email sender reports not configured without SMTP creds."""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    sender = EmailSender()
    assert not sender.is_configured()


def test_email_configured_with_credentials(monkeypatch):
    """Email sender reports configured with SMTP creds."""
    monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")
    sender = EmailSender()
    assert sender.is_configured()


def test_email_send_raises_without_config():
    """Sending without config raises EmailError."""
    sender = EmailSender()
    with pytest.raises(EmailError):
        sender.send("subj", "body")


def test_daily_briefing_formatted_correctly():
    """Briefing text is formatted with optional sections."""
    from brain.communication_hub.notification_formatter import format_briefing
    result = format_briefing("Summary", ["Section 1", "Section 2"])
    assert "Summary" in result
    assert "Section 1" in result
    assert "Section 2" in result
