"""Exceptions for Reticular Formation module."""


class ReticularFormationError(Exception):
    """Base exception for Reticular Formation module."""
    pass


class WakeWordError(ReticularFormationError):
    """Wake word detector error."""
    pass


class AttentionError(ReticularFormationError):
    """Attention manager error."""
    pass


class IdleError(ReticularFormationError):
    """Idle detector error."""
    pass


class InterruptError(ReticularFormationError):
    """Interrupt handler error."""
    pass
