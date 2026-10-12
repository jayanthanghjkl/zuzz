"""Cerebrum — Main Thinking Engine.

Runs conversations on local Ollama models or free cloud providers. The Cerebrum
does not decide *which* backend to use; the Thalamus tells it, by passing the
routing level.

Cloud requests prefer the dedicated connectors in the Corpus Callosum and fall
back to LiteLLM. Local requests use Ollama directly.

Response enhancement (Phase 4.5): The Cerebrum can optionally enhance replies
with thinking visualization, confidence scoring, context continuation, proactive
suggestions, self-critique, and mood-adaptive tone via the ResponseEnhancer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.brainstem.activity_logger import get_logger
from brain.cerebellum.response_enhancer import ResponseEnhancer, EnhancedResponse
from brain.cerebrum.backends import (
    DEFAULT_LOCAL_MODEL,
    BackendError,
    CerebrumError,
    call_litellm,
    call_local,
    InferenceRefused,
)
from brain.cerebrum.prompts import build_messages
from brain.cerebrum.reasoning import estimate_confidence, list_local_models
from brain.cerebrum.streaming import CerebrumStreaming
from brain.corpus_callosum.cloud_bridge import get_bridge
from brain.corpus_callosum.connector_state import ConnectorError
from brain.hippocampus.hippocampus import Hippocampus
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_manager import TrustManager
from core.config import model_for_level, provider_for_level, timeout_for_level

__all__ = ["Cerebrum", "CerebrumError", "InferenceRefused", "EnhancedResponse"]


class Cerebrum(CerebrumStreaming):
    """Main reasoning engine for the ZUZZ digital brain.

    Attributes:
        history: Recent conversation, oldest-first, capped by the settings.
        bridge: Corpus Callosum bridge used for cloud requests.
        last_provider: Provider that produced the most recent reply.
        enhancer: Optional ResponseEnhancer for Jarvis-style enhancements.
    """

    def __init__(
        self,
        history_limit: int = 20,
        gate: Optional[PermissionGate] = None,
        trust_level: Optional[int] = None,
        hippocampus: Optional[Hippocampus] = None,
        response_enhancer: Optional[ResponseEnhancer] = None,
        enable_enhancement: bool = True,
    ) -> None:
        """Initialise the engine.

        Args:
            history_limit: Maximum conversation messages kept in memory.
            gate: The Prefrontal gate consulted before any inference, or None to
                use the shared one. Injection rather than construction so the
                Cerebrum is never holding a *second* gate with its own reading of
                the matrix — one gate, one answer, as with the Hypothalamus.
            trust_level: Trust level used for those decisions, or None to read the
                shared trust manager at call time. Read per call rather than
                captured at construction so a trust change takes effect on the
                next request instead of the next restart.
            hippocampus: Optional Hippocampus for context continuation.
            response_enhancer: Optional pre-built ResponseEnhancer, injected by
                the composition root so the app's Hippocampus is the one the
                enhancer reads. When omitted one is built from ``hippocampus``.
            enable_enhancement: Whether to enable response enhancement (Phase 4.5).
        """
        self.history: List[Dict[str, str]] = []
        self.history_limit = history_limit
        self.bridge = get_bridge()
        self.last_provider = ""
        self.logger = get_logger()
        self.gate = gate if gate is not None else PermissionGate()
        self._trust_level = trust_level
        if response_enhancer is not None:
            self.enhancer = response_enhancer
        elif enable_enhancement:
            self.enhancer = ResponseEnhancer(hippocampus=hippocampus)
        else:
            self.enhancer = None

    def _current_trust(self) -> int:
        """Return the trust level to decide against.

        The injected level wins when given, which is how tests pin it. Otherwise a
        manager is built per call, which is cheap and correct rather than a
        cached value that would go stale: ``TrustManager.current`` re-reads the
        persisted level every time, so a level the operator changed mid-session is
        honoured on the very next request.
        """
        if self._trust_level is not None:
            return self._trust_level
        try:
            return int(TrustManager().current())
        except Exception:
            # A trust level that cannot be read must not make every answer fail.
            # Level 1 is the only level that cannot surprise the user.
            return 1

    def _check_may_infer(self, message: str, level: int) -> None:
        """Refuse to run inference the machine has not authorised.

        The kill switch is the reason this exists. Every other route into ZUZZ
        passes the gate on the way to the Body layer, but the Cerebrum is
        reachable directly — any caller holding a reference, including future code
        and a plugin — and inference was the one thing that answered regardless
        of what the switch said. A stop that does not stop the machine's voice is
        not a stop.

        Nothing about ordinary chat is asked for: ``chat`` is ``auto`` at every
        trust level, because generating an answer is not a side effect. What this
        buys is the switch, and the immutable rules that no trust level reaches.

        Args:
            message: The user message, used as the action's target.
            level: Routing level, recorded for the audit trail.

        Raises:
            InferenceRefused: If the kill switch is engaged or a rule forbids
                inference.
        """
        reason = self.gate.stopped_reason()
        if reason:
            raise InferenceRefused(f"Inference refused — {reason}")
        action = Action(
            category=ActionCategory.CHAT,
            target=message,
            detail={"level": level},
        )
        decision = self.gate.decide(action, self._current_trust())
        if not getattr(decision, "allowed", True):
            raise InferenceRefused(
                f"Inference refused — {getattr(decision, 'reason', 'gate refused')}"
            )

    def reset(self) -> None:
        """Clear the in-memory conversation history."""
        self.history = []

    def remember(self, role: str, content: str) -> None:
        """Append one message to the conversation history.

        Args:
            role: ``"user"`` or ``"assistant"``.
            content: Message text.
        """
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit :]

    async def think_local(
        self, message: str, level: int = 1, context: Optional[str] = None
    ) -> str:
        """Generate an answer using a local Ollama model.

        Args:
            message: The user message.
            level: Routing level, selects the local model size.
            context: Optional situational context.

        Returns:
            The generated reply.

        Raises:
            InferenceRefused: If the kill switch is engaged or a rule forbids
                inference.
            CerebrumError: If Ollama is unreachable or returns nothing usable.
        """
        self._check_may_infer(message, level)
        model = model_for_level(level) or DEFAULT_LOCAL_MODEL
        messages = build_messages(message, self.history, context, self.history_limit)
        try:
            content = await call_local(model, messages, level)
        except BackendError as exc:
            raise CerebrumError(str(exc)) from exc
        self.last_provider = "ollama"
        self.remember("user", message)
        self.remember("assistant", content)
        return content

    async def think_cloud(
        self, message: str, level: int = 3, context: Optional[str] = None
    ) -> str:
        """Generate an answer using a free cloud provider.

        Dedicated connectors in the Corpus Callosum are tried first; LiteLLM is
        kept as a fallback for providers that have no connector yet.

        Args:
            message: The user message.
            level: Routing level, selects the cloud model.
            context: Optional situational context.

        Returns:
            The generated reply.

        Raises:
            InferenceRefused: If the kill switch is engaged or a rule forbids
                inference.
            CerebrumError: If every cloud path fails.
        """
        self._check_may_infer(message, level)
        messages = build_messages(message, self.history, context, self.history_limit)
        try:
            result = await self.bridge.chat(messages, timeout=timeout_for_level(level))
            reply = str(result["reply"])
            if reply.strip():
                self.last_provider = str(result["provider"])
                self.remember("user", message)
                self.remember("assistant", reply)
                return reply
        except ConnectorError as exc:
            self.logger.log("cerebrum", "connector_path_failed", error=str(exc))

        try:
            reply = await call_litellm(messages, level)
        except BackendError as exc:
            raise CerebrumError(str(exc)) from exc
        self.last_provider = "litellm"
        self.remember("user", message)
        self.remember("assistant", reply)
        return reply

    async def think(
        self, message: str, level: int = 1, context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Answer a message using the backend implied by the routing level.

        Args:
            message: The user message.
            level: Routing level 1-5 chosen by the Thalamus.
            context: Optional situational context.

        Returns:
            Dict with ``reply``, ``level``, ``provider``, ``model`` and
            ``confidence``.

        Raises:
            CerebrumError: If the selected backend fails.
        """
        if provider_for_level(level) == "cloud":
            self.last_provider = ""
            reply = await self.think_cloud(message, level, context)
            provider = self.last_provider or "cloud"
            model = self._model_label(provider, level)
        else:
            reply = await self.think_local(message, level, context)
            provider, model = "ollama", self._model_label("ollama", level)
        return {
            "reply": reply,
            "level": level,
            "provider": provider,
            "model": model,
            "confidence": estimate_confidence(reply, len(message)),
        }

    def _model_label(self, provider: str, level: int) -> str:
        """Return the model name to report for a provider."""
        connector = self.bridge.connectors.get(provider)
        if connector is not None:
            return connector.model_for()
        return model_for_level(level) or "unknown"

    async def local_models(self) -> List[str]:
        """Return the model names Ollama currently has downloaded."""
        return await list_local_models()

    def enhance_response(
        self,
        message: str,
        reply: str,
        context: str = "",
        token_probs: Optional[List[float]] = None,
    ) -> EnhancedResponse:
        """Enhance a raw reply with Jarvis-style enhancements.

        Args:
            message: The user's message.
            reply: The raw model reply.
            context: Optional system context for critique.
            token_probs: Optional token probabilities for confidence scoring.

        Returns:
            EnhancedResponse with all enhancement artifacts.
        """
        if self.enhancer is None:
            # Return a minimal enhanced response without enhancements
            from brain.cerebellum.emotion_responder import detect_user_mood, NEUTRAL
            from brain.cerebellum.confidence_scorer import ConfidenceResult
            return EnhancedResponse(
                reply=reply,
                mood=detect_user_mood(message),
                confidence=ConfidenceResult(score=0.5, factors={}, label="medium"),
            )
        return self.enhancer.enhance(message, reply, context, token_probs)

    async def think_enhanced(
        self, message: str, level: int = 1, context: Optional[str] = None
    ) -> EnhancedResponse:
        """Answer a message and return the fully enhanced response.

        Args:
            message: The user message.
            level: Routing level 1-5 chosen by the Thalamus.
            context: Optional situational context.

        Returns:
            EnhancedResponse with reply, thinking steps, confidence, suggestions, etc.

        Raises:
            CerebrumError: If the selected backend fails.
        """
        if provider_for_level(level) == "cloud":
            self.last_provider = ""
            reply = await self.think_cloud(message, level, context)
            provider = self.last_provider or "cloud"
            model = self._model_label(provider, level)
        else:
            reply = await self.think_local(message, level, context)
            provider, model = "ollama", self._model_label("ollama", level)

        # Build base result
        base_result = {
            "reply": reply,
            "level": level,
            "provider": provider,
            "model": model,
            "confidence": estimate_confidence(reply, len(message)),
        }

        # Enhance with Jarvis features
        enhanced = self.enhance_response(message, reply, context)

        # Merge base metadata
        enhanced.metadata.update({
            "level": level,
            "provider": provider,
            "model": model,
            "base_confidence": base_result["confidence"],
        })

        return enhanced
