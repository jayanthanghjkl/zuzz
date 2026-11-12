"""Explicitly unavailable desktop boundary for headless servers."""

from typing import Any, Tuple
from .exceptions import ActuatorError


class UnavailableActuator:
    """Permit non-desktop services to start; never pretend input was sent."""

    def __init__(self, reason: str) -> None:
        """Retain the actionable initialization failure."""
        self.reason = reason

    def size(self) -> Tuple[int, int]:
        """Report no connected controllable display."""
        return (0, 0)

    def __getattr__(self, name: str) -> Any:
        """Reject every attempted desktop operation with the original cause."""
        raise ActuatorError(f"Desktop control unavailable: {self.reason}")
