"""Background Agents - custom exceptions."""

from __future__ import annotations


class BackgroundAgentError(Exception):
    """Base class for background agent errors."""


class AgentNotFoundError(BackgroundAgentError):
    """Agent not found in registry."""


class DeploymentError(BackgroundAgentError):
    """Agent deployment failed."""


class SyncError(BackgroundAgentError):
    """Agent result sync failed."""


class SchedulingError(BackgroundAgentError):
    """Agent scheduling failed."""
