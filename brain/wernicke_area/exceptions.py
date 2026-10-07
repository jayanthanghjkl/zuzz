"""Exceptions for Wernicke's Area module."""


class WernickeAreaError(Exception):
    """Base exception for Wernicke's Area module."""
    pass


class IntentParseError(WernickeAreaError):
    """Intent parsing error."""
    pass


class ContextError(WernickeAreaError):
    """Context resolution error."""
    pass


class AmbiguityError(WernickeAreaError):
    """Ambiguity detection error."""
    pass
