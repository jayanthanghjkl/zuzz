"""Cerebellum — proactive suggestion.

Generates follow-up suggestions based on the current conversation, the user's
history, and common patterns. Suggestions are surfaced as lightweight cards in
the UI — the user can click to accept or ignore.

This module is read-only and heuristic: it proposes, never acts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brain.hippocampus.hippocampus import Hippocampus

__all__ = [
    "ProactiveSuggester",
    "Suggestion",
    "generate_suggestions",
]

#: Suggestion templates keyed by detected intent.
_SUGGESTION_TEMPLATES: Dict[str, List[str]] = {
    "coding": [
        "Run the tests to verify the fix",
        "Add a test case for this edge case",
        "Refactor the helper into its own module",
        "Check for similar issues in related files",
    ],
    "debugging": [
        "Add logging to trace the execution path",
        "Check the full stack trace for the root cause",
        "Try reproducing with a minimal example",
        "Look for recent changes that could have caused this",
    ],
    "learning": [
        "Save this as a remembered fact for later",
        "Create a routine for this workflow",
        "Bookmark the relevant documentation",
    ],
    "planning": [
        "Break this down into smaller steps",
        "Identify the dependencies first",
        "Set up a checklist for the implementation",
    ],
    "troubleshooting": [
        "Check the service status page",
        "Review the recent logs for errors",
        "Try the operation in a clean environment",
    ],
    "generic": [
        "Ask for clarification on any part",
        "Request a code example",
        "Explore alternative approaches",
    ],
}

#: Keywords that map to suggestion categories.
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "coding": ["code", "function", "class", "module", "refactor", "implement", "write", "script", "api", "endpoint"],
    "debugging": ["error", "bug", "crash", "fail", "exception", "traceback", "debug", "fix", "broken", "issue"],
    "learning": ["how to", "what is", "explain", "learn", "understand", "tutorial", "guide"],
    "planning": ["plan", "design", "architecture", "approach", "strategy", "roadmap", "steps"],
    "troubleshooting": ["not working", "won't start", "connection", "timeout", "permission", "access denied"],
}


@dataclass
class Suggestion:
    """A proactive suggestion for the user.

    Attributes:
        text: The suggestion text.
        category: The category it belongs to.
        confidence: 0.0–1.0, how relevant the suggester thinks this is.
        action: Optional tool name if the suggestion maps to an action.
    """
    text: str
    category: str
    confidence: float
    action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "category": self.category,
            "confidence": round(self.confidence, 2),
            "action": self.action,
        }


class ProactiveSuggester:
    """Generates contextual follow-up suggestions."""

    def __init__(
        self,
        hippocampus: Optional[Hippocampus] = None,
        max_suggestions: int = 3,
    ) -> None:
        self.hippocampus = hippocampus
        self.max_suggestions = max_suggestions

    def categorize(self, message: str, reply: str) -> str:
        """Classify the conversation into a suggestion category."""
        combined = (message + " " + reply).lower()
        scores: Dict[str, int] = {}
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            scores[cat] = sum(1 for kw in keywords if kw in combined)
        if not scores or max(scores.values()) == 0:
            return "generic"
        return max(scores, key=scores.get)

    def get_history_based_suggestions(self, message: str) -> List[Suggestion]:
        """Generate suggestions based on user's past preferences and patterns."""
        if self.hippocampus is None:
            return []
        try:
            # Recall user preferences and recent tasks
            prefs = self.hippocampus.get_user_profile()
            suggestions = []
            # If user has a preferred editor, suggest related actions
            if prefs.get("preferences", {}).get("editor"):
                suggestions.append(Suggestion(
                    text=f"Open this in {prefs['preferences']['editor']}",
                    category="workflow",
                    confidence=0.6,
                    action="open_app",
                ))
            # If user has remembered facts about the project
            facts = prefs.get("facts", [])
            if facts:
                suggestions.append(Suggestion(
                    text="Apply remembered project conventions",
                    category="workflow",
                    confidence=0.5,
                ))
            return suggestions[:2]
        except Exception:
            return []

    def generate(
        self,
        message: str,
        reply: str,
        confidence_score: float = 0.5,
    ) -> List[Suggestion]:
        """Generate suggestions for a completed exchange."""
        category = self.categorize(message, reply)
        templates = _SUGGESTION_TEMPLATES.get(category, _SUGGESTION_TEMPLATES["generic"])

        suggestions: List[Suggestion] = []

        # Template-based suggestions (higher confidence for high-confidence replies)
        base_conf = 0.7 if confidence_score > 0.7 else 0.5
        for i, tmpl in enumerate(templates[:self.max_suggestions]):
            suggestions.append(Suggestion(
                text=tmpl,
                category=category,
                confidence=base_conf - (i * 0.1),
            ))

        # History-based suggestions
        history_suggestions = self.get_history_based_suggestions(message)
        suggestions.extend(history_suggestions)

        # Sort by confidence, deduplicate by text
        seen = set()
        unique = []
        for s in sorted(suggestions, key=lambda x: x.confidence, reverse=True):
            if s.text not in seen:
                seen.add(s.text)
                unique.append(s)

        return unique[:self.max_suggestions]


def generate_suggestions(
    message: str,
    reply: str,
    hippocampus: Optional[Hippocampus] = None,
    confidence_score: float = 0.5,
) -> List[Suggestion]:
    """Convenience function to generate suggestions."""
    suggester = ProactiveSuggester(hippocampus)
    return suggester.generate(message, reply, confidence_score)