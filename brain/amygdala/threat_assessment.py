"""Amygdala — Threat Assessment.

The value object the Amygdala produces: a score, a level, what matched, and an
explanation a human can read.

The Amygdala scores before the Prefrontal Cortex decides, and it can only ever
*raise* scrutiny. A high score forces confirmation or blocks; nothing here can
make an action easier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

SAFE = "safe"
CAUTION = "caution"
DANGEROUS = "dangerous"
FORBIDDEN = "forbidden"

MAX_SCORE = 10

#: Levels at or above this one cannot be auto-approved, whatever the trust
#: level says. Trust level 5 is autonomy over *routine* tasks, not permission
#: to do dangerous things silently.
CONFIRMATION_FLOOR = DANGEROUS

#: The bands from the approved plan, used when no config is supplied so that a
#: missing or malformed ``levels`` block cannot collapse everything to "safe".
DEFAULT_BANDS: Dict[str, List[int]] = {
    SAFE: [0, 2],
    CAUTION: [3, 5],
    DANGEROUS: [6, 8],
    FORBIDDEN: [9, 10],
}

_LEVEL_ORDER = {SAFE: 0, CAUTION: 1, DANGEROUS: 2, FORBIDDEN: 3}


def level_for(score: int, config: Dict[str, Any] | None = None) -> str:
    """Map a score onto a threat level.

    Args:
        score: Threat score.
        config: Optional ``levels`` mapping from ``danger_patterns.json``.

    Returns:
        One of ``safe``, ``caution``, ``dangerous`` or ``forbidden``.
    """
    bands = (config or {}).get("levels") or DEFAULT_BANDS
    for level, bounds in bands.items():
        low, high = bounds
        if low <= score <= high:
            return level
    return FORBIDDEN if score >= 9 else SAFE


def at_least(level: str, floor: str) -> bool:
    """Return True when a level is at or above a floor.

    Args:
        level: The level to test.
        floor: The threshold level.

    Returns:
        True when ``level`` is as severe as, or worse than, ``floor``.
    """
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(floor, 0)


@dataclass(frozen=True)
class ThreatAssessment:
    """How dangerous one action looks.

    Attributes:
        score: Threat score, 0 to 10.
        level: ``safe``, ``caution``, ``dangerous`` or ``forbidden``.
        matched: Ids of the patterns that fired.
        explanation: One line a user can read.
        target: What was assessed.
        context: Notes on how context changed the reading.
        requires_confirmation: True when the level forces a human check.
        veto: True when the level is ``forbidden``.
    """

    score: int
    level: str
    matched: List[str] = field(default_factory=list)
    explanation: str = ""
    target: str = ""
    context: List[str] = field(default_factory=list)
    forced_confirmation: bool = False

    @property
    def veto(self) -> bool:
        """Return True when this assessment forbids the action outright."""
        return self.level == FORBIDDEN

    @property
    def requires_confirmation(self) -> bool:
        """Return True when the level forces confirmation regardless of trust.

        Either the score reached the confirmation floor, or a destructive
        pattern fired — immutable rule R4 makes destructive commands ask at
        every trust level, including 5.
        """
        return self.forced_confirmation or at_least(self.level, CONFIRMATION_FLOOR)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API and the log."""
        return {
            "score": self.score,
            "level": self.level,
            "matched": list(self.matched),
            "explanation": self.explanation,
            "target": self.target,
            "context": list(self.context),
            "veto": self.veto,
            "requires_confirmation": self.requires_confirmation,
            "forced_confirmation": self.forced_confirmation,
        }