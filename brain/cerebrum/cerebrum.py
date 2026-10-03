"""Cerebrum — Main Thinking Engine.

Runs conversations on local Ollama models or free cloud providers. The Cerebrum
does not decide *which* backend to use; the Thalamus tells it, by passing the
routing level.

Cloud requests prefer the dedicated connectors in the Corpus Callosum and fall
back to LiteLLM. Local requests use Ollama directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.brainstem.activity_logger import get_logger
from brain.cerebrum.backends import (
    DEFAULT_LOCAL_MODEL,
    BackendError,
    CerebrumError,
    call_litellm,
    call_local,
)
from brain.cerebrum.prompts import build_messages
from brain.cerebrum.reasoning import estimate_confidence, list_local_models
from brain.cerebrum.streaming import CerebrumStreaming
from brain.corpus_callosum.cloud_bridge import get_bridge
from brain.corpus_callosum.connector_state import ConnectorError
from core.config import model_for_level, provider_for_level, timeout_for_level

__all__ = ["Cerebrum", "CerebrumError"]


class Cerebrum(CerebrumStreaming):
    """Main reasoning engine for the ZUZZ digital brain.

    Attributes:
        history: Recent conversation, oldest-first, capped by the settings.
        bridge: Corpus Callosum bridge used for cloud requests.
        last_provider: Provider that produced the most recent reply.
    """

    def __init__(self, history_limit: int = 20) -> None:
        """Initialise the engine.

        Args:
            history_limit: Maximum conversation messages kept in memory.
        """
        self.history: List[Dict[str, str]] = []
        self.history_limit = history_limit
        self.bridge = get_bridge()
        self.last_provider = ""
        self.logger = get_logger()

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
            CerebrumError: If Ollama is unreachable or returns nothing usable.
        """
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
            CerebrumError: If every cloud path fails.
        """
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
