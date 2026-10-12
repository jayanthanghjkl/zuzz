"""Cerebellum — thinking display.

Streams the model's internal reasoning as discrete, labelled steps so the UI can
show a live "thinking" indicator. Each step is a short, self-contained sentence
the model emits before the final answer, not a replacement for the answer itself.

The Cerebrum does not decide *what* to think; it decides *when* to surface the
thinking. The model writes it naturally in the stream, and this module extracts
and labels it for the transport layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "ThinkingStep",
    "ThinkingDisplay",
    "extract_thinking_steps",
    "THINKING_PATTERNS",
]

#: Patterns that mark a sentence as a thinking step rather than prose.
#: Ordered by specificity — the first match wins.
THINKING_PATTERNS: List[tuple[str, str]] = [
    (r"^Let me (?:think|consider|check|verify|look|search|calculate)", "planning"),
    (r"^I (?:need to|should|will|must) (?:first|check|verify|look|search)", "planning"),
    (r"^First,? (?:I|let me)", "planning"),
    (r"^Next,? (?:I|let me)", "planning"),
    (r"^Then,? (?:I|let me)", "planning"),
    (r"^Checking (?:the|for|if|whether)", "verification"),
    (r"^Verifying (?:the|that|if|whether)", "verification"),
    (r"^Looking (?:at|for|up)", "retrieval"),
    (r"^Searching (?:for|the)", "retrieval"),
    (r"^Calculating", "computation"),
    (r"^Computing", "computation"),
    (r"^Analysing", "analysis"),
    (r"^Analyzing", "analysis"),
    (r"^Reasoning", "analysis"),
    (r"^Recalling", "memory"),
    (r"^Remembering", "memory"),
    (r"^Based on", "synthesis"),
    (r"^Given (?:that|the)", "synthesis"),
    (r"^So (?:the|it|we)", "synthesis"),
    (r"^Therefore,?", "synthesis"),
    (r"^This (?:means|suggests|indicates)", "synthesis"),
    (r"^The (?:result|answer|solution)", "conclusion"),
]


@dataclass
class ThinkingStep:
    """One labelled reasoning step emitted by the model.

    Attributes:
        text: The step text, without the leading marker.
        kind: The step category — planning, retrieval, verification, etc.
        index: Position in the sequence (0-based).
    """
    text: str
    kind: str
    index: int


@dataclass
class ThinkingDisplay:
    """Collects and emits thinking steps from a token stream.

    Usage:
        display = ThinkingDisplay()
        async for token in cerebrum.stream(message):
            step = display.feed(token)
            if step:
                yield {"type": "thinking", "step": step}
            else:
                yield {"type": "token", "content": token}
    """
    steps: List[ThinkingStep] = field(default_factory=list)
    _buffer: str = ""
    _step_index: int = 0

    def feed(self, token: str) -> Optional[Dict[str, Any]]:
        """Feed one token, return a step dict if a step completed.

        Args:
            token: The next text fragment from the model stream.

        Returns:
            A dict with keys ``text``, ``kind``, ``index`` when a step
            boundary is detected, otherwise ``None``.
        """
        self._buffer += token
        # Look for sentence boundaries (., !, ? followed by space or end)
        sentences = re.split(r"(?<=[.!?])\s+", self._buffer.strip())
        if len(sentences) > 1:
            # All but the last are complete sentences
            for sent in sentences[:-1]:
                step = self._classify_step(sent.strip())
                if step:
                    self.steps.append(step)
                    self._step_index += 1
                    return {
                        "text": step.text,
                        "kind": step.kind,
                        "index": step.index,
                    }
            # Keep the last (potentially incomplete) sentence in buffer
            self._buffer = sentences[-1]
        return None

    def _classify_step(self, sentence: str) -> Optional[ThinkingStep]:
        """Classify a sentence as a thinking step or prose."""
        if not sentence or len(sentence) < 5:
            return None
        # Filter out obvious prose (answers, greetings, etc.)
        prose_starts = (
            "here is", "here are", "the answer", "in summary", "to summarize",
            "in conclusion", "overall", "finally", "so the answer", "thus the",
            "therefore the", "that's why", "this is because", "the reason"
        )
        lowered = sentence.lower()
        if any(lowered.startswith(p) for p in prose_starts):
            return None
        for pattern, kind in THINKING_PATTERNS:
            if re.search(pattern, sentence, re.IGNORECASE):
                return ThinkingStep(text=sentence, kind=kind, index=self._step_index)
        return None

    def flush(self) -> List[Dict[str, Any]]:
        """Return any remaining buffer as a final step if it looks like thinking."""
        if self._buffer.strip():
            step = self._classify_step(self._buffer.strip())
            if step:
                self.steps.append(step)
                return [{
                    "text": step.text,
                    "kind": step.kind,
                    "index": step.index,
                }]
        return []

    def reset(self) -> None:
        """Clear collected steps and buffer for a new response."""
        self.steps = []
        self._buffer = ""
        self._step_index = 0


def extract_thinking_steps(text: str) -> List[ThinkingStep]:
    """Extract all thinking steps from a complete response (non-streaming).

    Args:
        text: The full response text.

    Returns:
        List of ThinkingStep objects in order.
    """
    display = ThinkingDisplay()
    # Feed the whole text at once for simplicity
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        step = display._classify_step(sentence.strip())
        if step:
            display.steps.append(step)
    return display.steps