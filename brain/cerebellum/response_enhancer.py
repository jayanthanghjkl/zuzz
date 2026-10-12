"""Cerebellum — response enhancer orchestrator.

The single entry point for Jarvis-style response enhancement. Coordinates all
enhancement modules: thinking display, confidence scoring, context continuation,
proactive suggestions, self-critique, and personality variations.

This is the only module the Cerebrum imports. It exposes one method:
    enhanced = enhancer.enhance(message, reply, context)

And returns a structured result with all enhancement artifacts for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brain.cerebellum.confidence_scorer import ConfidenceScorer, ConfidenceResult, score_reply
from brain.cerebellum.context_continuer import ContextContinuer, ContinuationContext, get_continuation_context
from brain.cerebellum.emotion_responder import detect_user_mood, FRUSTRATED, EXCITED, TIRED, NEUTRAL, MOODS
from brain.cerebellum.personality_variations import PersonalityVariations, apply_variation
from brain.cerebellum.proactive_suggester import ProactiveSuggester, Suggestion, generate_suggestions
from brain.cerebellum.self_critic import SelfCritic, CritiqueResult, critique_reply
from brain.cerebellum.thinking_display import ThinkingDisplay, ThinkingStep, extract_thinking_steps
from brain.hippocampus.hippocampus import Hippocampus

__all__ = [
    "ResponseEnhancer",
    "EnhancedResponse",
    "enhance_response",
]

#: Appended to a reply whose confidence tier is "low". Short, honest,
#: and separate from the answer so the answer itself is unchanged.
LOW_CONFIDENCE_DISCLAIMER = (
    "\n\n*(low confidence — worth verifying before relying on this)*"
)


def _with_disclaimer(reply: str) -> str:
    """Append the low-confidence disclaimer, once.

    Args:
        reply: The reply to mark.

    Returns:
        The reply with the disclaimer appended. Idempotent: a reply
        that already carries it is returned unchanged, so a pipeline
        that enhances twice does not stack two disclaimers.
    """
    text = str(reply or "").strip()
    if "low confidence" in text.lower():
        return reply
    return f"{text}{LOW_CONFIDENCE_DISCLAIMER}"


@dataclass
class EnhancedResponse:
    """Complete enhanced response with all Jarvis artifacts.

    Attributes:
        reply: The final enhanced reply text.
        mood: Detected user mood.
        thinking_steps: Extracted thinking steps (if streaming).
        confidence: Confidence score and breakdown.
        continuation: Context continuation data (if applicable).
        suggestions: Proactive follow-up suggestions.
        critique: Self-critique flags.
        variation_meta: Personality variation metadata for UI.
        metadata: Additional debugging info.
    """
    reply: str
    mood: str
    thinking_steps: List[ThinkingStep] = field(default_factory=list)
    confidence: Optional[ConfidenceResult] = None
    continuation: Optional[ContinuationContext] = None
    suggestions: List[Suggestion] = field(default_factory=list)
    critique: Optional[CritiqueResult] = None
    variation_meta: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "mood": self.mood,
            "thinking_steps": [
                {"text": s.text, "kind": s.kind, "index": s.index}
                for s in self.thinking_steps
            ],
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "continuation": {
                "is_continuation": self.continuation.is_continuation if self.continuation else False,
                "summary": self.continuation.summary if self.continuation else "",
                "memory_count": len(self.continuation.memories) if self.continuation else 0,
            } if self.continuation else None,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "critique": self.critique.to_dict() if self.critique else None,
            "variation_meta": self.variation_meta,
            "metadata": self.metadata,
        }


class ResponseEnhancer:
    """Orchestrates all response enhancement modules.

    Usage:
        enhancer = ResponseEnhancer(hippocampus=app.state.hippocampus)
        result = enhancer.enhance(user_message, raw_reply, context=system_context)
        # result.reply is the final text
        # result.to_dict() has everything for the UI
    """

    def __init__(
        self,
        hippocampus: Optional[Hippocampus] = None,
        enable_thinking: bool = True,
        enable_confidence: bool = True,
        enable_continuation: bool = True,
        enable_suggestions: bool = True,
        enable_critique: bool = True,
        enable_variations: bool = True,
    ) -> None:
        self.hippocampus = hippocampus
        self.enable_thinking = enable_thinking
        self.enable_confidence = enable_confidence
        self.enable_continuation = enable_continuation
        self.enable_suggestions = enable_suggestions
        self.enable_critique = enable_critique
        self.enable_variations = enable_variations

        # Initialize submodules
        self._thinking = ThinkingDisplay()
        self._confidence = ConfidenceScorer()
        self._continuation = ContextContinuer(hippocampus)
        self._suggester = ProactiveSuggester(hippocampus)
        self._critic = SelfCritic()
        self._variations = PersonalityVariations()

    def enhance(
        self,
        message: str,
        reply: str,
        context: str = "",
        token_probs: Optional[List[float]] = None,
        stream_thinking: bool = False,
    ) -> EnhancedResponse:
        """Run the full enhancement pipeline on a reply.

        Args:
            message: The user's message.
            reply: The raw model reply.
            context: Optional system context (for critique).
            token_probs: Optional token probabilities for confidence.
            stream_thinking: If True, thinking steps are extracted but the
                reply is assumed to be pre-streamed (thinking already emitted).

        Returns:
            EnhancedResponse with all artifacts.
        """
        # 1. Detect mood (used by multiple modules)
        mood = detect_user_mood(message)

        # 2. Context continuation (may add context to reply)
        continuation = None
        enhanced_reply = reply
        if self.enable_continuation:
            continuation = self._continuation.get_context(message)
            if continuation.is_continuation and continuation.context_text:
                # Prepend context to the reply for the model's awareness
                # But we don't modify the reply here — the context is for the
                # *next* turn's system prompt. We just flag it.
                pass

        # 3. Personality variation (mood-adaptive rewrite)
        if self.enable_variations:
            enhanced_reply = apply_variation(enhanced_reply, mood)

        # 4. Confidence scoring
        confidence = None
        if self.enable_confidence:
            confidence = self._confidence.score(enhanced_reply, message, token_probs)

        # 5. Proactive suggestions
        suggestions = []
        if self.enable_suggestions and confidence:
            suggestions = self._suggester.generate(message, enhanced_reply, confidence.score)

        # 6. Self-critique
        critique = None
        if self.enable_critique:
            critique = self._critic.critique(enhanced_reply, message, context)
            # If critique has blocking errors, we could regenerate, but for now
            # we just flag them. The Cerebrum decides whether to retry.

        # 7. Thinking steps (extract from reply if not streaming)
        thinking_steps = []
        if self.enable_thinking and not stream_thinking:
            thinking_steps = extract_thinking_steps(enhanced_reply)
            # Remove thinking steps from the final reply (they were for display)
            for step in thinking_steps:
                enhanced_reply = enhanced_reply.replace(step.text, "", 1)
            enhanced_reply = enhanced_reply.strip()

        # 8. Low-confidence disclaimer. An answer ZUZZ is not sure of
        # says so, because the alternative is a confident-sounding wrong
        # answer, which is strictly worse than an honest uncertain one.
        if confidence is not None and confidence.label == "low":
            enhanced_reply = _with_disclaimer(enhanced_reply)

        # 9. Variation metadata for UI
        variation_meta = self._variations.get_variation_set(mood).meta

        return EnhancedResponse(
            reply=enhanced_reply,
            mood=mood,
            thinking_steps=thinking_steps,
            confidence=confidence,
            continuation=continuation,
            suggestions=suggestions,
            critique=critique,
            variation_meta=variation_meta,
            metadata={
                "continuation_triggered": continuation.is_continuation if continuation else False,
                "critique_blocking": critique.has_blocking if critique else False,
                "suggestion_count": len(suggestions),
                "thinking_step_count": len(thinking_steps),
            },
        )

    def enhance_streaming(
        self,
        message: str,
        token: str,
        is_final: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Process one token during streaming.

        Returns a thinking event dict if a step completed, None otherwise.
        On final token, returns the full EnhancedResponse.
        """
        if not self.enable_thinking:
            return None

        step = self._thinking.feed(token)
        if step:
            return {"type": "thinking", "step": step}

        if is_final:
            # Flush remaining
            steps = self._thinking.flush()
            if steps:
                return {"type": "thinking", "step": steps[0]}
            # Reset for next response
            self._thinking.reset()

        return None

    def reset_streaming(self) -> None:
        """Reset streaming state for a new response."""
        self._thinking.reset()


def enhance_response(
    message: str,
    reply: str,
    hippocampus: Optional[Hippocampus] = None,
    context: str = "",
    token_probs: Optional[List[float]] = None,
) -> EnhancedResponse:
    """Convenience function for one-shot enhancement."""
    enhancer = ResponseEnhancer(hippocampus=hippocampus)
    return enhancer.enhance(message, reply, context, token_probs)