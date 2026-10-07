"""Communication Hub - custom exceptions."""

from __future__ import annotations


class CommunicationError(Exception):
    """Base class for communication hub errors."""


class TelegramError(CommunicationError):
    """Telegram bot error."""


class EmailError(CommunicationError):
    """Email sending error."""


class PushError(CommunicationError):
    """Push notification error."""


class EscalationError(CommunicationError):
    """Escalation chain error."""


class AgentError(Exception):
    """Base class for background agent errors."""


class DeploymentError(AgentError):
    """Agent deployment error."""


class SyncError(AgentError):
    """Agent sync error."""


class SchedulingError(AgentError):
    """Agent scheduling error."""
