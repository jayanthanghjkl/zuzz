"""Thalamus — Smart Router.

The single entry point for every request in ZUZZ. It classifies the message,
picks a backend, asks the Cerebrum for an answer, and falls back gracefully
when the preferred backend is down. No other component may route a request.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from brain.brainstem.activity_logger import get_logger
from brain.cerebrum.cerebrum import Cerebrum, CerebrumError, InferenceRefused
from brain.thalamus.complexity_analyzer import ComplexityAnalyzer
from brain.thalamus.load_balancer import LoadBalancer

#: Which kind of work each routing level implies, so the Hypothalamus can be
#: asked about the right thing. Levels 1 and 2 are local, 3 and above are the
#: cloud; the mapping matters only for the local ones, because that is where a
#: VRAM refusal changes the answer.
_TASK_FOR_LEVEL = {
    1: "fast_responses",
    2: "complex_responses",
    3: "complex_responses",
    4: "deep_reasoning",
    5: "deep_reasoning",
}


class Router:
    """Routes messages between local and cloud brains.

    Attributes:
        cerebrum: The reasoning engine that produces answers.
        analyzer: Message complexity classifier.
        balancer: Cloud provider health tracker.
        hypothalamus: Optional resource layer consulted before local routing.
    """

    def __init__(
        self,
        cerebrum: Optional[Cerebrum] = None,
        analyzer: Optional[ComplexityAnalyzer] = None,
        hypothalamus: Any = None,
    ) -> None:
        """Build the router.

        Args:
            cerebrum: Optional existing engine to reuse.
            analyzer: Optional existing analyzer to reuse.
            hypothalamus: Optional
                :class:`~brain.hypothalamus.hypothalamus.Hypothalamus` consulted
                before a local level is attempted. Defaults to None, which
                disables the check entirely and leaves routing exactly as it was —
                so the resource layer can be removed without touching this file.
        """
        self.cerebrum = cerebrum or Cerebrum()
        self.analyzer = analyzer or ComplexityAnalyzer()
        self.hypothalamus = hypothalamus
        self.balancer = LoadBalancer(self.cerebrum.bridge.configured_providers())
        self.logger = get_logger()

    def classify(self, message: str, internet_available: bool = True) -> Dict[str, Any]:
        """Classify a message without answering it.

        Args:
            message: Raw user text.
            internet_available: Whether cloud providers can be reached.

        Returns:
            The routing decision as a plain dict.
        """
        decision = self.analyzer.analyze(message, internet_available)
        return {
            "level": decision.level,
            "reason": decision.reason,
            "signals": decision.signals,
            "offline_fallback": decision.offline_fallback,
        }

    async def route(self, message: str, internet_available: bool = True) -> Dict[str, Any]:
        """Classify and answer a message, with fallback on failure.

        Args:
            message: Raw user text.
            internet_available: Whether cloud providers can be reached.

        Returns:
            Dict with ``reply``, ``level`` (the level that actually
            answered), ``classified_level`` (what the analyzer chose),
            ``reason``, ``provider``, ``model``, ``confidence`` and
            ``degraded`` (True if a fallback backend had to be used).

        Raises:
            ValueError: If the message is empty.
            CerebrumError: If every backend fails.
        """
        if not message.strip():
            raise ValueError("Cannot route an empty message")

        decision = self.analyzer.analyze(message, internet_available)
        self.logger.log(
            "thalamus",
            "route",
            level=decision.level,
            reason=decision.reason,
            signals=decision.signals,
            chars=len(message),
        )

        order = self._fallback_order(decision.level, internet_available)
        errors: list[str] = []
        resource_skips: list[str] = []

        # Every local level on the ladder is checked, not just the first rung.
        # A cloud level degrades into progressively smaller local models, so the
        # retries reach local inference just as surely as the first choice does,
        # and a retry made with no VRAM left is the failure this whole check
        # exists to prevent.
        runnable: list[int] = []
        for level in order:
            blocked = self._resource_block(level)
            if blocked:
                resource_skips.append(f"level {level}: {blocked}")
                self.logger.log("thalamus", "resource_reroute", level=level, reason=blocked)
            else:
                runnable.append(level)
        order = runnable
        if not order:
            raise CerebrumError(
                "No backend could run the task — " + "; ".join(resource_skips)
            )

        for index, level in enumerate(order):
            try:
                result = await self.cerebrum.think(message, level)
                provider = str(result["provider"])
                if index:
                    self.balancer.report_failure(provider)
                else:
                    self.balancer.report_success(provider)
                result.update(
                    {
                        "reason": decision.reason,
                        "classified_level": decision.level,
                        # A level skipped for resources means the answer came from
                        # somewhere other than the one that was asked for, which is
                        # degradation even though no backend ever failed.
                        "degraded": index > 0 or bool(resource_skips),
                        "attempts": index + 1,
                        "resource_skips": resource_skips,
                    }
                )
                self.logger.log(
                    "thalamus",
                    "answer",
                    level=level,
                    provider=provider,
                    degraded=index > 0,
                    errors=errors,
                )
                return result
            except InferenceRefused:
                # A refusal is not a backend failure, so the ladder must not walk.
                # Retrying here would ask a stopped machine for an answer once
                # per level, and would then report the result as "all backends
                # failed" — telling the caller to try again, which is the opposite
                # of what a stop means. Rethrown untouched so the route can
                # answer 409.
                raise
            except CerebrumError as exc:
                errors.append(f"level {level}: {exc}")
                self.logger.log("thalamus", "backend_failure", level=level, error=str(exc))

        raise CerebrumError("All backends failed — " + "; ".join(errors))

    async def route_stream(
        self, message: str, internet_available: bool = True
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream a reply, yielding fragments and a final metadata event.

        Args:
            message: Raw user text.
            internet_available: Whether cloud providers can be reached.

        Yields:
            Dicts shaped ``{"type": "token", "content": ...}`` while streaming
            and one final ``{"type": "done", ...}`` with routing metadata.

        Raises:
            CerebrumError: If the local backend fails mid-stream.
        """
        decision = self.analyzer.analyze(message, internet_available)
        level = decision.level
        if level > 2 and not self.cerebrum.bridge.configured_providers():
            level = 2
        # Streaming consults the Hypothalamus too. Streaming is the path the chat
        # UI actually uses, so skipping the check here would leave the primary
        # route through the brain with no thermal or VRAM protection — exactly the
        # case the check exists for, running local inference on a machine that is
        # already too hot or out of room.
        #
        # A refusal escalates to the cloud instead of raising, because the caller
        # is already committed to receiving a stream: a stream that raises before
        # its first token surfaces as a broken response, whereas one that arrives
        # from the cloud is merely slower to start.
        blocked = self._resource_block(level) if level <= 2 else ""
        resource_skips: list[str] = []
        if blocked:
            resource_skips.append(f"level {level}: {blocked}")
            self.logger.log("thalamus", "resource_reroute", level=level, reason=blocked)
            cloud_level = next(
                (candidate for candidate in (3, 4) if candidate > level), 3
            )
            if self.cerebrum.bridge.configured_providers():
                level = cloud_level
                self.logger.log(
                    "thalamus",
                    "stream_escalated",
                    from_level=decision.level,
                    to_level=level,
                    reason=blocked,
                )
            else:
                # Nowhere to escalate to. Say so rather than streaming from a
                # machine that cannot afford it.
                raise CerebrumError(
                    "Local inference is unavailable and no cloud provider is "
                    f"configured — {blocked}"
                )
        collected = ""
        try:
            async for fragment in self.cerebrum.stream(message, level):
                collected += fragment
                yield {"type": "token", "content": fragment}
        except CerebrumError as exc:
            raise CerebrumError(f"Streaming failed: {exc}") from exc

        yield {
            "type": "done",
            "level": decision.level,
            "streamed_level": level,
            "reason": decision.reason,
            "provider": self.cerebrum.last_provider,
            "chars": len(collected),
            "degraded": level != decision.level,
            "resource_skips": resource_skips,
        }

    def _resource_block(self, level: int) -> str:
        """Return why a level cannot be attempted now, or an empty string.

        Two conditions send work away from local inference:

        * **The hardware is too hot**, or a thermal sensor is unreadable. Local
          inference is the thing generating the heat, so continuing is the cause
          of the condition.
        * **The task will not fit the VRAM budget.** Attempting it anyway is what
          the budget exists to prevent: running out of VRAM mid-inference can
          take the GPU driver down with it.

        Returns:
            The reason to record, or an empty string when the level is fine or no
            Hypothalamus is attached.
        """
        if self.hypothalamus is None:
            return ""
        brain = self.hypothalamus
        # Levels 1 and 2 are the local ones. Everything above is answered by a
        # cloud provider, which costs no local VRAM, so the budget has nothing to
        # say about it — asking anyway would refuse a cloud request because the
        # GPU is busy and then fall straight through to a local attempt, which is
        # the opposite of what the budget is for.
        local = level <= 2
        try:
            if local and brain.models.cloud_only:
                return "thermal state is critical; local inference is suspended"
            if not local:
                return ""
            plan = brain.request_resources(_TASK_FOR_LEVEL.get(level, "chat"))
        except Exception as exc:
            # A resource check that cannot answer must not block the answer.
            self.logger.log("thalamus", "resource_check_failed", error=str(exc))
            return ""
        if plan["route"] == "local":
            return ""
        return f"{plan['reason']}: not attempting local inference"

    def check_level_allowed(self, level: int) -> str:
        """Return why a level may not be attempted right now, or an empty string.

        The public form of the check the routing paths use internally, so a caller
        that honours an explicitly requested level — rather than choosing one —
        can ask the same question the router would have asked. Without this, a
        caller holding a Cerebrum and a level could reach local inference having
        consulted nothing but the kill switch: the Cerebrum knows about stopping,
        not about whether the machine can afford the work.

        Args:
            level: The routing level about to be used.

        Returns:
            The reason it is refused, or an empty string when it is allowed.
        """
        return self._resource_block(level)

    def _fallback_order(self, level: int, internet_available: bool) -> list[int]:
        """Build the list of levels to try, best first.

        Cloud levels degrade to progressively smaller local models, and local
        levels escalate to the cloud expert if the local model is not
        downloaded or fails. Cloud levels are skipped entirely when no
        provider credential is configured.

        Args:
            level: The level the analyzer chose.
            internet_available: Whether cloud providers can be reached.

        Returns:
            Levels in attempt order, with no duplicates.
        """
        cloud_ready = bool(self.cerebrum.bridge.configured_providers())
        if not internet_available or not cloud_ready:
            return list(dict.fromkeys([min(level, 2), 2, 1]))

        if level >= 3:
            return list(dict.fromkeys([level, 2, 1]))

        return list(dict.fromkeys([level, 2, 3, 1]))

    def status(self) -> Dict[str, Any]:
        """Return router status for the dashboard."""
        return {
            "providers": self.balancer.status(),
            "history_size": len(self.cerebrum.history),
            "last_provider": self.cerebrum.last_provider,
        }
