"""Cerebellum — personality variations.

Provides mood-adaptive response variations that go beyond the base tone
adjustment. Selects from pre-defined response "modes" based on the user's
detected mood, the conversation context, and the configured personality.

This is the layer that makes ZUZZ sound like it *understands* the user's
emotional state, not just that it detected it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brain.cerebellum.emotion_responder import FRUSTRATED, EXCITED, TIRED, NEUTRAL, MOODS
from brain.cerebellum.personality_config import Personality

__all__ = [
    "PersonalityVariations",
    "VariationSet",
    "select_variation",
    "apply_variation",
]

#: Response variations for each mood. Each variation is a set of adjustments
#: that can be composed onto a base reply.
_VARIATIONS: Dict[str, Dict[str, List[str]]] = {
    FRUSTRATED: {
        "openers": [
            "",  # No opener — direct answer
            "Here's the fix:",
            "The issue is",
            "Short answer:",
        ],
        "closers": [
            "",
            "Let me know if that works.",
            "Try that and tell me what happens.",
        ],
        "modifiers": [
            "trim_preamble",
            "shorten_sentences",
            "remove_hedging",
            "lead_with_answer",
        ],
    },
    EXCITED: {
        "openers": [
            "Great — ",
            "Nice! ",
            "That worked — ",
            "Excellent, ",
        ],
        "closers": [
            " 🎉",
            " — glad that helped!",
            " — happy to help!",
        ],
        "modifiers": [
            "match_energy",
            "brief_celebration",
        ],
    },
    TIRED: {
        "openers": [
            "",
            "Quick answer: ",
            "TL;DR: ",
        ],
        "closers": [
            "",
            " — that's it.",
            " Let me know if you need more.",
        ],
        "modifiers": [
            "trim_preamble",
            "cap_sentences_3",
            "remove_options",
            "remove_suggestions",
        ],
    },
    NEUTRAL: {
        "openers": [
            "",
        ],
        "closers": [
            "",
        ],
        "modifiers": [],
    },
}

#: Variation metadata for UI display.
_VARIATION_META: Dict[str, Dict[str, str]] = {
    FRUSTRATED: {"label": "Direct", "icon": "⚡", "description": "Brief, no preamble, leads with answer"},
    EXCITED: {"label": "Energetic", "icon": "✨", "description": "Matches user energy, brief celebration"},
    TIRED: {"label": "Low-friction", "icon": "🌙", "description": "Minimal, capped at 3 sentences"},
    NEUTRAL: {"label": "Balanced", "icon": "💬", "description": "Standard configured voice"},
}


@dataclass
class VariationSet:
    """A complete variation profile for a mood.

    Attributes:
        mood: The mood this variation applies to.
        openers: Alternative opening phrases.
        closers: Alternative closing phrases.
        modifiers: Transformation names to apply.
        meta: Display metadata for the UI.
    """
    mood: str
    openers: List[str]
    closers: List[str]
    modifiers: List[str]
    meta: Dict[str, str]


class PersonalityVariations:
    """Selects and applies mood-appropriate response variations."""

    def __init__(self, personality: Optional[Personality] = None) -> None:
        self.personality = personality

    def get_variation_set(self, mood: str) -> VariationSet:
        """Return the variation set for a mood."""
        v = _VARIATIONS.get(mood, _VARIATIONS[NEUTRAL])
        meta = _VARIATION_META.get(mood, _VARIATION_META[NEUTRAL])
        return VariationSet(
            mood=mood,
            openers=v["openers"],
            closers=v["closers"],
            modifiers=v["modifiers"],
            meta=meta,
        )

    def select_opener(self, mood: str, reply: str) -> str:
        """Choose an appropriate opener for the mood and reply."""
        variation = self.get_variation_set(mood)
        # For frustrated/tired, prefer empty opener if reply already starts with substance
        if mood in (FRUSTRATED, TIRED):
            if reply and not reply[0].isupper():
                return variation.openers[0]  # Empty
        return variation.openers[0]  # Default to first

    def select_closer(self, mood: str, reply: str) -> str:
        """Choose an appropriate closer for the mood."""
        variation = self.get_variation_set(mood)
        return variation.closers[0]  # Default to first

    def apply_modifiers(self, reply: str, mood: str) -> str:
        """Apply the mood's modifiers to a reply."""
        variation = self.get_variation_set(mood)
        text = reply

        for modifier in variation.modifiers:
            text = self._apply_modifier(text, modifier, mood)

        return text

    def _apply_modifier(self, text: str, modifier: str, mood: str) -> str:
        """Apply a single named modifier."""
        import re

        if modifier == "trim_preamble":
            # Remove filler openers
            text = re.sub(
                r"^(?:sure|certainly|of course|absolutely|no problem|got it|understood|"
                r"i\s+am\s+sorry|i'?m\s+sorry|apologies|i\s+understand|i\s+hear\s+you|"
                r"no\s+worries|that'?s\s+(?:frustrating|tough))\b[.!]?\s*",
                "", text, flags=re.IGNORECASE
            ).strip()

        elif modifier == "shorten_sentences":
            # Compress verbose phrasing
            text = re.sub(r"\b(in order to)\b", "to", text, flags=re.IGNORECASE)
            text = re.sub(r"\b(due to the fact that)\b", "because", text, flags=re.IGNORECASE)
            text = re.sub(r"\b(at this point in time)\b", "now", text, flags=re.IGNORECASE)

        elif modifier == "remove_hedging":
            # Remove hedging words
            text = re.sub(r"\b(?:probably|maybe|perhaps|might|could)\s+", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\b(?:I think|I believe|I guess)\s+", "", text, flags=re.IGNORECASE)

        elif modifier == "lead_with_answer":
            # Ensure the first sentence contains the answer, not context
            sentences = re.split(r"(?<=[.!?])\s+", text)
            if len(sentences) > 1:
                # If first sentence is context, swap with second
                first = sentences[0].lower()
                context_starts = ("the reason", "this is because", "basically", "essentially")
                if any(first.startswith(cs) for cs in context_starts):
                    sentences[0], sentences[1] = sentences[1], sentences[0]
                    text = " ".join(sentences)

        elif modifier == "match_energy":
            # Add an exclamation if the reply is flat
            if text and not text.endswith(("!", "?")):
                text = text.rstrip(".") + "!"

        elif modifier == "brief_celebration":
            # Ensure celebration is only one sentence
            pass  # Handled by opener/closer selection

        elif modifier == "cap_sentences_3":
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
            if len(sentences) > 3:
                text = " ".join(sentences[:3])

        elif modifier == "remove_options":
            # Remove "you could also..." style suggestions
            text = re.sub(
                r"(?:you (?:could|might|can) also|alternatively|another option|"
                r"if you (?:want|prefer)|as an alternative)[^.]*[.!]",
                "", text, flags=re.IGNORECASE
            ).strip()

        elif modifier == "remove_suggestions":
            # Remove proactive suggestions
            text = re.sub(
                r"(?:i (?:suggest|recommend|advise)|try (?:also|this)|consider)[^.]*[.!]",
                "", text, flags=re.IGNORECASE
            ).strip()

        return text

    def enhance(self, reply: str, mood: str) -> str:
        """Full enhancement pipeline: opener + modifiers + closer."""
        variation = self.get_variation_set(mood)
        text = reply.strip()

        # Apply modifiers first
        text = self.apply_modifiers(text, mood)

        # Add opener if not empty and reply doesn't already start strongly
        opener = self.select_opener(mood, text)
        if opener and not text.startswith(opener.strip()):
            text = f"{opener} {text}".strip()

        # Add closer
        closer = self.select_closer(mood, text)
        if closer and not text.endswith(closer.strip()):
            text = f"{text} {closer}".strip()

        return text


def select_variation(mood: str) -> VariationSet:
    """Convenience: get the variation set for a mood."""
    variations = PersonalityVariations()
    return variations.get_variation_set(mood)


def apply_variation(reply: str, mood: str, personality: Optional[Personality] = None) -> str:
    """Convenience: apply mood variation to a reply."""
    variations = PersonalityVariations(personality)
    return variations.enhance(reply, mood)