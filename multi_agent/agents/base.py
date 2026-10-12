"""Multi-Agent Specialist — base class for all specialist agents."""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.cerebrum.cerebrum import Cerebrum

__all__ = ["BaseAgent"]


class BaseAgent:
    """Base class for all specialist agents.

    Every specialist agent wraps the Cerebrum and implements a single
    ``execute`` method that takes a task instruction and optional context,
    and returns a structured result.
    """

    role: str = "base"
    description: str = "Base specialist agent"

    def __init__(self, cerebrum: Cerebrum) -> None:
        """Initialize with a Cerebrum instance.

        Args:
            cerebrum: The shared Cerebrum instance for model access.
        """
        self.cerebrum = cerebrum

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """Execute the specialist task.

        Args:
            task: The instruction for this specialist.
            context: Optional context from previous agents or the user.

        Returns:
            Structured result (dict, string, or any JSON-serializable type).
        """
        raise NotImplementedError