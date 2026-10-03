"""Thalamus — Complexity Analyzer.

Scores a user message and assigns it to one of five routing levels. This is
the decision point described in PROJECT.md section 9: nothing in ZUZZ routes
around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

SIMPLE_PATTERNS = (
    r"^\s*(hi|hey|hello|yo|thanks|thank you|ok|okay|cool|bye)\b",
    r"^\s*(what is|who is|when is|where is)\b.{0,60}\?*\s*$",
    r"^\s*(open|launch|start|list|show|close)\s+(the\s+)?\w{1,20}\s*$",
)

MEDIUM_PATTERNS = (
    r"\b(write|create|make|generate|refactor|rename|convert)\b.{0,80}\b(script|code|file|function|list|table|json|yaml)\b",
    r"\b(organi[sz]e|sort|clean up|rename all|find all)\b",
    r"\b(explain|summari[sz]e|translate|rewrite)\b",
)

COMPLEX_PATTERNS = (
    r"\b(debug|fix the bug|trace|root cause|why does|why is)\b",
    r"\b(algorithm|complexity|optimi[sz]e|architecture|database|async|concurrency)\b",
    r"\b(quantum|neural networks?|transformers?|cryptography|compilers?|recursion|"
    r"distributed systems?|microservices?|time complexity|big o)\b",
    r"```",
)

# Conceptual questions get longer as the topic gets harder; a bare "explain
# this" stays medium but "explain how transformers compute attention" does not.
DEEP_QUESTION_STARTERS = (
    r"^\s*(explain|describe|how does|how do|why does|why is|why are|compare|"
    r"walk me through|teach me|what happens)"
)

VERY_COMPLEX_PATTERNS = (
    r"\b(build|create|design)\b.{0,60}\b(full app|application|system|platform|api service|web app)\b",
    r"\b(multi[- ]?step|end[- ]to[- ]end|from scratch|complete|entire)\b",
    r"\b(research|investigate|compare)\b.{0,60}\b(companies|options|alternatives|libraries|tools)\b",
)

CONTINUOUS_PATTERNS = (
    r"\b(every day|daily|hourly|nightly|weekly|monitor|keep an eye on|track prices|watch for)\b",
    r"\b(schedule|recurring|ongoing|background|automatic(ally)?)\b",
)

CODE_BLOCK = re.compile(r"```")
URL = re.compile(r"https?://\S+")


@dataclass
class Complexity:
    """Routing decision for one message.

    Attributes:
        level: Routing level 1-5.
        reason: Human-readable explanation of the level chosen.
        signals: Which patterns matched.
        offline_fallback: True when the level was forced down to local.
    """

    level: int
    reason: str
    signals: List[str] = field(default_factory=list)
    offline_fallback: bool = False


class ComplexityAnalyzer:
    """Classifies messages into routing levels using weighted pattern rules."""

    LEVELS = (
        (5, CONTINUOUS_PATTERNS, "continuous task — background agent"),
        (4, VERY_COMPLEX_PATTERNS, "very complex — cloud agent team"),
        (3, COMPLEX_PATTERNS, "complex reasoning — cloud expert"),
        (2, MEDIUM_PATTERNS, "medium task — local 3B model"),
        (1, SIMPLE_PATTERNS, "simple command — local 1.5B model"),
    )

    def analyze(self, message: str, internet_available: bool = True) -> Complexity:
        """Classify one message.

        Args:
            message: Raw user text.
            internet_available: When False the level is capped at 2 because
                cloud providers cannot be reached.

        Returns:
            The routing decision.
        """
        text = message.strip()
        signals: List[str] = []
        level = 2
        reason = "default — medium task"

        for candidate, patterns, why in self.LEVELS:
            matched = [p for p in patterns if re.search(p, text, re.IGNORECASE)]
            if matched:
                level, reason, signals = candidate, why, matched
                break

        signals.extend(self._structural_signals(text))

        if level <= 2 and self._is_deep_question(text):
            level, reason = 3, "conceptual question - cloud expert"

        if len(text) > 400 and level < 3:
            level, reason = 3, "long input - cloud expert"

        offline = False
        if not internet_available and level > 2:
            level, offline = 2, True
            reason += " (forced local: no internet)"

        return Complexity(level=level, reason=reason, signals=signals, offline_fallback=offline)

    @staticmethod
    def _is_deep_question(text: str) -> bool:
        """Return True for a conceptual question with a substantial topic.

        Args:
            text: The raw user message.

        Returns:
            True when the message opens like a deep question and is long enough
            to imply a non-trivial topic.
        """
        return bool(
            re.search(DEEP_QUESTION_STARTERS, text, re.IGNORECASE) and len(text) > 45
        )

    @staticmethod
    def _structural_signals(text: str) -> List[str]:
        """Return structural features that influence routing."""
        signals: List[str] = []
        if CODE_BLOCK.search(text):
            signals.append("code_block")
        if URL.search(text):
            signals.append("contains_url")
        question_count = text.count("?")
        if question_count >= 3:
            signals.append("multi_question")
        return signals
