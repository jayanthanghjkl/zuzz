"""Wernicke's Area - Deep language comprehension."""

from .wernicke_area import WernickeArea
from .intent_parser import IntentParser
from .context_resolver import ContextResolver
from .ambiguity_detector import AmbiguityDetector
from .exceptions import WernickeAreaError, IntentParseError, ContextError, AmbiguityError

__all__ = [
    "WernickeArea",
    "IntentParser",
    "ContextResolver",
    "AmbiguityDetector",
    "WernickeAreaError",
    "IntentParseError",
    "ContextError",
    "AmbiguityError",
]