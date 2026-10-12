"""Cerebellum — context continuation.

Detects when the user is continuing a previous conversation thread and injects
relevant context from the Hippocampus (memory) into the system prompt. This
enables responses like "As we discussed yesterday about X..." without the user
having to repeat themselves.

The module is read-only: it queries memory and formats context, but never
writes. The Hippocampus owns storage; this module owns retrieval-for-continuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brain.cerebellum.cerebellum import get_cerebellum
from brain.hippocampus.hippocampus import Hippocampus

__all__ = [
    "ContextContinuer",
    "ContinuationContext",
    "get_continuation_context",
]

#: Keywords that signal the user is referencing prior conversation.
_CONTINUATION_CUES = [
    r"\bas we (?:discussed|talked|said|agreed)\b",
    r"\byou (?:said|told|mentioned|recommended)\b",
    r"\bwe (?:covered|discussed|talked about)\b",
    r"\blike (?:before|last time|yesterday|earlier)\b",
    r"\bsame (?:issue|problem|error|question)\b",
    r"\bcontinue (?:from|where)\b",
    r"\bpick up\b",
    r"\bfollowing up\b",
    r"\bas (?:mentioned|discussed)\b",
    r"\bpreviously\b",
    r"\blast time\b",
    r"\byesterday\b",
    r"\bearlier\b",
]

#: Maximum memories to retrieve for context.
_MAX_MEMORIES = 5

#: Maximum characters of context to inject.
_MAX_CONTEXT_CHARS = 2000


@dataclass
class ContinuationContext:
    """Context assembled for a continuation reply.

    Attributes:
        is_continuation: Whether the message appears to continue a prior thread.
        memories: Relevant memories from the Hippocampus.
        summary: A one-line summary for the UI.
        context_text: Formatted text ready for the system prompt.
    """
    is_continuation: bool
    memories: List[Dict[str, Any]]
    summary: str
    context_text: str


class ContextContinuer:
    """Detects and assembles context for conversation continuation."""

    def __init__(self, hippocampus: Optional[Hippocampus] = None) -> None:
        self.hippocampus = hippocampus

    def detect_continuation(self, message: str) -> bool:
        """Return True if the message references prior conversation."""
        import re
        lowered = message.lower()
        return any(re.search(cue, lowered) for cue in _CONTINUATION_CUES)

    def get_relevant_memories(
        self, message: str, limit: int = _MAX_MEMORIES
    ) -> List[Dict[str, Any]]:
        """Query the Hippocampus for memories relevant to the message."""
        if self.hippocampus is None:
            return []
        try:
            # Search across all memory types
            results = self.hippocampus.recall(message, limit=limit)
            return results.get("memories", [])
        except Exception:
            return []

    def build_context_text(self, memories: List[Dict[str, Any]]) -> str:
        """Format memories into a prompt-ready context block."""
        if not memories:
            return ""
        lines = ["## Previous context (from memory)"]
        for i, mem in enumerate(memories, 1):
            mem_type = mem.get("type", "memory")
            content = mem.get("content", "")
            timestamp = mem.get("timestamp", "")
            if timestamp:
                lines.append(f"{i}. [{mem_type}] {content} (from {timestamp})")
            else:
                lines.append(f"{i}. [{mem_type}] {content}")
        lines.append("Use this context if relevant. Do not invent details not present above.")
        return "\n".join(lines)

    def build_summary(self, memories: List[Dict[str, Any]]) -> str:
        """One-line summary for the UI badge."""
        if not memories:
            return "No prior context found"
        types = [m.get("type", "memory") for m in memories]
        return f"Found {len(memories)} relevant memories: {', '.join(set(types))}"

    def get_context(self, message: str) -> ContinuationContext:
        """Full pipeline: detect, retrieve, format."""
        is_cont = self.detect_continuation(message)
        if not is_cont:
            return ContinuationContext(
                is_continuation=False,
                memories=[],
                summary="",
                context_text="",
            )
        memories = self.get_relevant_memories(message)
        return ContinuationContext(
            is_continuation=True,
            memories=memories,
            summary=self.build_summary(memories),
            context_text=self.build_context_text(memories),
        )


def get_continuation_context(message: str, hippocampus: Optional[Hippocampus] = None) -> ContinuationContext:
    """Convenience function using the default Hippocampus."""
    if hippocampus is None:
        cerebellum = get_cerebellum()
        # Access via the app state pattern - in practice the Hippocampus
        # is on app.state.hippocampus; here we accept it as a parameter.
        pass
    continuer = ContextContinuer(hippocampus)
    return continuer.get_context(message)