"""Cerebellum — confidence scoring.

Assigns a confidence score (0.0–1.0) to each model reply based on observable
signals: token probability (when available), answer length vs question
complexity, hedging language, self-corrections, and structural markers.

The score is a heuristic, not a probability. It is surfaced to the UI as a
badge so the user can weigh the answer — it never gates or filters anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

__all__ = [
    "ConfidenceScorer",
    "ConfidenceResult",
    "score_reply",
]

#: Words and phrases that lower confidence — hedging, uncertainty, filler.
_HEDGING_PATTERNS = [
    r"\bI think\b", r"\bI believe\b", r"\bI guess\b", r"\bprobably\b",
    r"\bmaybe\b", r"\bperhaps\b", r"\bseems like\b", r"\bappears to\b",
    r"\bmight be\b", r"\bcould be\b", r"\bnot sure\b", r"\bunsure\b",
    r"\bhard to say\b", r"\bdifficult to\b", r"\bunclear\b", r"\bambiguous\b",
]

#: Words and phrases that raise confidence — certainty, directness.
_CONFIDENCE_BOOSTERS = [
    r"\bdefinitely\b", r"\bcertainly\b", r"\bclearly\b", r"\bobviously\b",
    r"\bexactly\b", r"\bprecisely\b", r"\bthe answer is\b", r"\bis\b \d+",
    r"\bcorrect\b", r"\bconfirmed\b", r"\bverified\b",
]

#: Patterns indicating self-correction mid-reply.
_SELF_CORRECTION_PATTERNS = [
    r"wait,? (?:actually|let me)", r"actually,?", r"correction:?",
    r"my mistake", r"i was wrong", r"ignore (?:that|the previous)",
    r"let me rephrase", r"to be precise",
]

#: Structural markers of a well-formed answer.
_STRUCTURE_MARKERS = [
    r"^\d+\.", r"^[-*]\s", r"^Step \d+", r"^First[,:]", r"^Second[,:]",
    r"^Third[,:]", r"^Finally[,:]", r"^In short[,:]", r"^Summary[,:]",
]


@dataclass
class ConfidenceResult:
    """Confidence score with contributing factors.

    Attributes:
        score: 0.0–1.0 confidence level.
        factors: Dict of factor names to their contribution (-1.0 to +1.0).
        label: Human-readable tier — "low", "medium", "high".
    """
    score: float
    factors: Dict[str, float]
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "factors": self.factors, "label": self.label}


class ConfidenceScorer:
    """Scores a model reply for confidence.

    The algorithm combines multiple weak signals into a single score. Each
    signal contributes a bounded amount so no single cue dominates.
    """

    def __init__(
        self,
        base_score: float = 0.65,
        length_weight: float = 0.15,
        hedging_weight: float = 0.20,
        structure_weight: float = 0.10,
        correction_weight: float = 0.15,
        booster_weight: float = 0.10,
        high_threshold: float = 0.75,
        medium_threshold: float = 0.45,
    ) -> None:
        self.base_score = base_score
        self.length_weight = length_weight
        self.hedging_weight = hedging_weight
        self.structure_weight = structure_weight
        self.correction_weight = correction_weight
        self.booster_weight = booster_weight
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold

    def score(
        self,
        reply: str,
        question: str = "",
        token_probs: Optional[List[float]] = None,
    ) -> ConfidenceResult:
        """Compute confidence for a reply.

        Args:
            reply: The model's answer text.
            question: The user's question (for length/complexity ratio).
            token_probs: Optional per-token probabilities from the model.

        Returns:
            ConfidenceResult with score, factor breakdown, and label.
        """
        factors: Dict[str, float] = {}

        # 1. Token probability signal (if available)
        if token_probs:
            avg_prob = sum(token_probs) / len(token_probs)
            factors["token_probability"] = round((avg_prob - 0.5) * 2, 3)
        else:
            factors["token_probability"] = 0.0

        # 2. Length vs question complexity
        length_factor = self._score_length_ratio(reply, question)
        factors["length_ratio"] = round(length_factor, 3)

        # 3. Hedging language (reduces confidence)
        hedging_factor = self._score_hedging(reply)
        factors["hedging"] = round(hedging_factor, 3)

        # 4. Structural markers (increases confidence)
        structure_factor = self._score_structure(reply)
        factors["structure"] = round(structure_factor, 3)

        # 5. Self-corrections (reduces confidence)
        correction_factor = self._score_corrections(reply)
        factors["self_correction"] = round(correction_factor, 3)

        # 6. Confidence boosters
        booster_factor = self._score_boosters(reply)
        factors["boosters"] = round(booster_factor, 3)

        # Weighted combination
        score = self.base_score
        score += factors["token_probability"] * 0.2  # Small influence
        score += factors["length_ratio"] * self.length_weight
        score += factors["hedging"] * self.hedging_weight
        score += factors["structure"] * self.structure_weight
        score += factors["self_correction"] * self.correction_weight
        score += factors["boosters"] * self.booster_weight

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))

        # Label
        if score >= self.high_threshold:
            label = "high"
        elif score >= self.medium_threshold:
            label = "medium"
        else:
            label = "low"

        return ConfidenceResult(score=round(score, 3), factors=factors, label=label)

    def _score_length_ratio(self, reply: str, question: str) -> float:
        """Score based on answer length relative to question complexity."""
        if not question:
            return 0.0
        q_words = len(question.split())
        r_words = len(reply.split())
        if q_words == 0:
            return 0.0
        ratio = r_words / max(1, q_words)
        # Sweet spot: 2–10x question length. Too short = terse; too long = rambling.
        if 2 <= ratio <= 10:
            return 0.5
        elif 1 <= ratio < 2:
            return 0.0
        elif 10 < ratio <= 20:
            return 0.0
        else:
            return -0.3

    def _score_hedging(self, reply: str) -> float:
        """Negative score for hedging language."""
        count = sum(1 for p in _HEDGING_PATTERNS if re.search(p, reply, re.IGNORECASE))
        return max(-1.0, -0.15 * count)

    def _score_structure(self, reply: str) -> float:
        """Positive score for clear structure."""
        count = sum(1 for p in _STRUCTURE_MARKERS if re.search(p, reply, re.MULTILINE | re.IGNORECASE))
        return min(1.0, 0.15 * count)

    def _score_corrections(self, reply: str) -> float:
        """Negative score for mid-reply self-corrections."""
        count = sum(1 for p in _SELF_CORRECTION_PATTERNS if re.search(p, reply, re.IGNORECASE))
        return max(-1.0, -0.25 * count)

    def _score_boosters(self, reply: str) -> float:
        """Positive score for certainty language."""
        count = sum(1 for p in _CONFIDENCE_BOOSTERS if re.search(p, reply, re.IGNORECASE))
        return min(1.0, 0.15 * count)


def score_reply(
    reply: str,
    question: str = "",
    token_probs: Optional[List[float]] = None,
) -> ConfidenceResult:
    """Convenience function to score a reply with default settings."""
    scorer = ConfidenceScorer()
    return scorer.score(reply, question, token_probs)