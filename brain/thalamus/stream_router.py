"""Router-owned streaming with safe pre-output fallback and resource checks."""

from typing import Any, AsyncIterator, Dict, Optional

from brain.cerebrum.cerebrum import CerebrumError, InferenceRefused


class StreamRouter:
    """Share the normal router's selection policy with WebSocket chat."""

    async def route_stream(
        self, message: str, internet_available: bool = True,
        level: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream events; retry only before any user-visible model output.

        A failed partial reply is never completed or executed as tool calls.
        Forced levels are honored without silently choosing another model.
        """
        if not message.strip():
            raise ValueError("Cannot route an empty message")
        if level is not None and level not in range(1, 6):
            raise ValueError("Level must be between 1 and 5")
        decision = self.analyzer.analyze(message, internet_available)
        selected = level if level is not None else decision.level
        order = [level] if level is not None else self._fallback_order(selected, internet_available)
        errors, skips = [], []
        for attempt, candidate in enumerate(order):
            blocked = self._resource_block(candidate)
            if blocked:
                skips.append(f"level {candidate}: {blocked}")
                continue
            chars = 0
            emitted = False
            try:
                async for event in self.cerebrum.stream(message, candidate):
                    # Older injected engines may yield text; production yields events.
                    if isinstance(event, str):
                        event = {"type": "token", "content": event}
                    if event.get("type") == "done":
                        continue  # completion belongs to this router, not the engine
                    if event.get("type") == "token":
                        content = str(event.get("content", ""))
                        chars += len(content)
                        emitted = emitted or bool(content)
                    yield event
                if not chars:
                    raise CerebrumError("Model returned an empty response")
                yield {
                    "type": "done", "level": selected,
                    "streamed_level": candidate,
                    "reason": "forced level" if level is not None else decision.reason,
                    "provider": self.cerebrum.last_provider,
                    "chars": chars, "degraded": candidate != selected,
                    "attempts": attempt + 1, "resource_skips": skips,
                }
                return
            except InferenceRefused:
                raise
            except CerebrumError as exc:
                if emitted:
                    raise CerebrumError(f"Reply interrupted; no actions were run: {exc}") from exc
                errors.append(f"level {candidate}: {exc}")
        if skips and not errors and not self.cerebrum.bridge.configured_providers():
            raise CerebrumError("Local inference unavailable and no cloud provider is configured — " + "; ".join(skips))
        detail = "; ".join(errors + skips)
        raise CerebrumError(f"No AI backend could answer. Check Ollama/models or cloud credentials. {detail}")
