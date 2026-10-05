"""Amygdala — Threat Detector.

Scores an action 0-10 before the Prefrontal Cortex decides anything. The pattern
rules, weights and cache live in
:mod:`brain.amygdala.threat_patterns`; this module is the scoring itself.

Two ideas do the real work:

**Context, not just regex.** A regex that matches ``rm -rf`` will happily fire on
a file called ``notes about rm -rf.txt``, on a document whose *content* mentions
the phrase, and on a command scoped to the project's own build directory. Those
are not the same risk, and a detector that cannot tell them apart becomes noise
that gets ignored. So each pattern declares which action categories it applies
to: command patterns are never consulted for file actions, and a match that is
skipped for that reason is written down rather than dropped.

**Speed.** Patterns are compiled once and assessments are cached by
(category, target), because assessment sits in the request path of every action
and must stay under 50ms.
"""

from __future__ import annotations

from typing import List

from brain.amygdala.threat_assessment import MAX_SCORE, ThreatAssessment, level_for
from brain.amygdala.threat_patterns import (
    FORCE_CONFIRM_PATTERNS,
    POINTS,
    applies_to,
    cache_get,
    cache_put,
    compiled_patterns,
)
from brain.prefrontal_cortex.action import Action, ActionCategory
from core.config import get_danger_patterns
from core.paths import PROTECTED, TEMP, classify

__all__ = ["ThreatDetector", "clear_cache", "compiled_patterns", "applies_to", "POINTS"]


def clear_cache() -> None:
    """Empty the assessment cache."""
    from brain.amygdala.threat_patterns import clear_cache as _clear

    _clear()


class ThreatDetector:
    """Scores actions for danger."""

    def __init__(self) -> None:
        """Load the pattern configuration."""
        self.config = get_danger_patterns()

    def assess(self, action: Action) -> ThreatAssessment:
        """Score one action.

        Args:
            action: The proposed action.

        Returns:
            The assessment, carrying the score, level and what matched.
        """
        key = (str(action.category), action.target)
        cached = cache_get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        assessment = self._assess_uncached(action)
        cache_put(key, assessment)
        return assessment

    def _assess_uncached(self, action: Action) -> ThreatAssessment:
        """Score an action without consulting the cache."""
        category = str(action.category)
        text = action.target or ""
        matched: List[str] = []
        skipped: List[str] = []
        score = 0

        for pattern_id, pattern, categories, exempt in compiled_patterns():
            if not pattern.search(text):
                continue
            if applies_to(categories, category, exempt):
                score += POINTS.get(pattern_id, 0)
                matched.append(pattern_id)
            else:
                # Recorded rather than silent: a pattern that would have fired
                # on this text but does not apply to this kind of action is
                # exactly what makes the result trustworthy.
                skipped.append(pattern_id)

        context = self._skipped_notes(category, skipped)
        score = max(0, min(MAX_SCORE, score + self._path_adjustment(action, context)))
        level = level_for(score, self.config)
        return ThreatAssessment(
            score=score,
            level=level,
            matched=matched,
            explanation=self._explain(level, matched),
            target=action.target,
            context=context,
            forced_confirmation=bool(FORCE_CONFIRM_PATTERNS.intersection(matched)),
        )

    def _path_adjustment(self, action: Action, context: List[str]) -> int:
        """Adjust the score using what the path *is*, not how it is spelled.

        Deleting inside the project's own temp directory is a cleanup; the same
        delete against a system path is not.

        Args:
            action: The proposed action.
            context: Notes gathered so far, appended to.

        Returns:
            Points to add or subtract.
        """
        if not action.target:
            return 0
        kind = classify(action.target)
        if kind == PROTECTED:
            context.append("target is a protected system path")
            return 2
        if kind == TEMP and action.category == ActionCategory.DELETE_FILES:
            context.append("target is a disposable temp path, not user data")
            return -3
        return 0

    @staticmethod
    def _skipped_notes(category: str, skipped: List[str]) -> List[str]:
        """Record patterns that matched the text but do not apply here.

        Args:
            category: The action category under assessment.
            skipped: Ids of patterns that matched but were not applied.

        Returns:
            Notes for the audit trail.
        """
        if not skipped:
            return []
        return [
            f"ignored inapplicable patterns for a {category} action: "
            + ", ".join(sorted(set(skipped)))
        ]

    @staticmethod
    def _explain(level: str, matched: List[str]) -> str:
        """Return a one-line explanation of the score."""
        if not matched:
            return "no danger patterns matched"
        return f"level {level}: matched {', '.join(sorted(set(matched)))}"