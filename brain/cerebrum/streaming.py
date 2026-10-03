"""Cerebrum — Streaming.

The streaming half of the Cerebrum, split into a mixin so `cerebrum.py` stays
under the 200-line limit. Handles both transports: Ollama's NDJSON stream for
local models and the Corpus Callosum connectors' SSE stream for cloud providers.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List

from brain.cerebrum.backends import DEFAULT_LOCAL_MODEL, CerebrumError
from brain.cerebrum.prompts import build_messages
from brain.cerebrum.reasoning import stream_local
from brain.corpus_callosum.connector_state import ConnectorError
from core.config import model_for_level, provider_for_level, timeout_for_level


class CerebrumStreaming:
    """Streaming mixin for the Cerebrum.

Expects the host class to provide ``history``, ``history_limit``, ``bridge``,
``last_provider``, ``remember``, ``_check_may_infer`` and the model config
helpers.
"""

    async def stream(self, message: str, level: int = 1) -> AsyncIterator[str]:
        """Stream a reply token by token.

        Yields each fragment as it arrives rather than collecting the whole reply
        first. Collecting and returning made this an ordinary coroutine that
        yielded nothing, so ``async for`` over it raised ``TypeError`` — meaning
        the chat UI's own streaming path, the one most requests actually take,
        could not work at all. The conversation is still recorded once the
        stream ends, so history holds the complete answer.

        Args:
            message: The user message.
            level: Routing level.

        Yields:
            Text fragments.

        Raises:
            InferenceRefused: If the kill switch is engaged or a rule forbids
                inference.
            CerebrumError: If the selected backend fails.
        """
        # The same backstop the non-streaming paths carry. Checked here rather
        # than only in the Router, because the Cerebrum is directly reachable and
        # this is the path the chat UI actually uses — a stop that the streaming
        # route ignored would leave the machine still answering.
        self._check_may_infer(message, level)  # type: ignore[attr-defined]
        messages = build_messages(message, self.history, None, self.history_limit)  # type: ignore[attr-defined]
        collected: List[str] = []

        if provider_for_level(level) == "cloud":
            source = self._stream_cloud(messages, level)  # type: ignore[attr-defined]
        else:
            source = self._stream_local(message, messages, level)  # type: ignore[attr-defined]

        async for fragment in source:
            collected.append(fragment)
            yield fragment

        self.remember("user", message)  # type: ignore[attr-defined]
        self.remember("assistant", "".join(collected))  # type: ignore[attr-defined]

    async def _stream_cloud(self, messages: List[Dict[str, str]], level: int) -> AsyncIterator[str]:
        """Stream from the Corpus Callosum bridge.

        Args:
            messages: Chat messages including the system prompt.
            level: Routing level, used for the timeout.

        Yields:
            Text fragments.

        Raises:
            CerebrumError: If every configured provider fails.
        """
        try:
            async for event in self.bridge.stream(messages, timeout=timeout_for_level(level)):  # type: ignore[attr-defined]
                self.last_provider = str(event["provider"])  # type: ignore[attr-defined]
                yield str(event["content"])
        except ConnectorError as exc:
            raise CerebrumError(str(exc)) from exc

    async def _stream_local(
        self, message: str, messages: List[Dict[str, str]], level: int
    ) -> AsyncIterator[str]:
        """Stream from local Ollama.

        Args:
            message: The user message, used only for error context.
            messages: Chat messages including the system prompt.
            level: Routing level, selects the model and timeout.

        Yields:
            Text fragments.

        Raises:
            CerebrumError: If Ollama is unreachable or errors mid-stream.
        """
        model = model_for_level(level) or DEFAULT_LOCAL_MODEL
        try:
            async for fragment in stream_local(model, messages, timeout_for_level(level)):
                self.last_provider = "ollama"  # type: ignore[attr-defined]
                yield fragment
        except (ConnectionError, RuntimeError) as exc:
            raise CerebrumError(str(exc)) from exc
