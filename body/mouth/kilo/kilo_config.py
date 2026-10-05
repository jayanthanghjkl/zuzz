"""Body/Mouth/Kilo — model routing.

Which model answers an agent, and what happens when it will not.

**Groq first, local second, and the second is there for one specific reason.** Groq's
free tier allows 30 requests a minute and a five-agent pipeline issues a dozen or more
per task. A pipeline that stalls on the ninth call is worse than one that is slightly
slower throughout, so an HTTP 429 falls back to a local Ollama model rather than
propagating. Every *other* connector error is raised as
:class:`~body.mouth.kilo.exceptions.ModelUnavailable`, because silently falling back on
a malformed-request error would hide a prompt the model cannot parse.

The connector is called directly rather than through
:meth:`~brain.corpus_callosum.cloud_bridge.CloudBridge.chat`, because the bridge's job is
to *avoid* rate limits by walking its fallback order — and here the rate limit is the
signal being acted on. Letting the bridge absorb it would mean the 429 never reaches
the one module that can do something about it.

This module holds no gate. It takes the decision its caller already verified, because
the decision that permits an agent to reason is the decision that permits its output
to be used, and minting a second one here would be the Body allowing itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brain.cerebrum.backends import BackendError, call_local
from brain.corpus_callosum.cloud_bridge import get_bridge
from brain.corpus_callosum.connector_state import (
    ConnectorError,
    NotConfiguredError,
    RateLimitedError,
)

from body.mouth.kilo.exceptions import DecisionRefused, ModelUnavailable
from body.mouth.kilo.kilo_agents import AGENT_NAMES, Agent, get as get_agent

__all__ = ["InferenceResult", "KiloConfig", "get_config"]

#: Cloud provider tried first. Named rather than discovered, because the point of the
#: fallback is that the *first* attempt is known rather than searched for.
PRIMARY_PROVIDER = "groq"

#: Cloud timeout. Long enough for a 120B model to write a file, short enough that a
#: hung request does not hold the user's attention for the rest of the task.
CLOUD_TIMEOUT = 120

#: Prompt cap in characters. Applied whole rather than mid-token: half a tool call is
#: worse than a smaller complete one, because the model is then confidently wrong.
MAX_PROMPT = 24_000

#: Reply cap in characters, applied to whatever comes back.
MAX_REPLY = 32_000


@dataclass(frozen=True)
class InferenceResult:
    """One answer from one model.

    Attributes:
        text: The reply.
        provider: ``groq``, ``ollama``, or whatever answered.
        model: The model that answered it.
        fell_back: True when the cloud provider declined and a local model answered.
        reason: Why the fallback happened, or an empty string.
    """

    text: str
    provider: str
    model: str
    fell_back: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return the result as plain data for the API and the log."""
        return {"provider": self.provider, "model": self.model,
                "fell_back": self.fell_back, "reason": self.reason,
                "characters": len(self.text)}


class KiloConfig:
    """Model routing and output caps for the coding agents."""

    def __init__(self, logger: Optional[Any] = None) -> None:
        """Wire the config to the bridge it routes through.

        Args:
            logger: Activity logger, so a fallback is recorded rather than being
                visible only in the agent log on the Code page.
        """
        self.logger = logger
        self.bridge = get_bridge()

    # -- routing ---------------------------------------------------------

    def route(self, name: str) -> Dict[str, Any]:
        """Return where an agent's inference would go. Not gated; a gauge.

        Args:
            name: The agent name.

        Returns:
            The agent's model and fallback, plus which cloud providers hold a
            credential right now — the last field being why a fallback may be about
            to happen rather than why one did.
        """
        configured = self.bridge.configured_providers()
        return {
            **get_agent(name).describe(),
            "primary_provider": PRIMARY_PROVIDER,
            "configured_providers": configured,
            "cloud_available": PRIMARY_PROVIDER in configured,
        }

    def routing(self) -> Dict[str, Any]:
        """Return the routing table for every agent. Not gated; a gauge."""
        return {
            "primary_provider": PRIMARY_PROVIDER,
            "agents": [self.route(name) for name in AGENT_NAMES],
        }

    # -- inference -------------------------------------------------------

    async def infer(
        self,
        decision: Any,
        agent: str,
        prompt: str,
        system: str = "",
        timeout: Optional[int] = None,
    ) -> InferenceResult:
        """Ask an agent's model to answer.

        Args:
            decision: The caller's already-verified decision. Required and checked
                for presence, not for permission — the caller decided that, and
                re-deciding here would be a second reading of the matrix.
            agent: The agent name, used for its model, temperature and fallback.
            prompt: The user message.
            system: Extra system text appended to the agent's own prompt.
            timeout: Override the cloud timeout.

        Returns:
            The answer, and which model gave it.

        Raises:
            DecisionRefused: No decision was passed. Inference is the most expensive
                thing this package does; a caller reaching it empty-handed is a bug,
                not a shortcut.
            ModelUnavailable: The cloud provider failed for a reason that is not a
                rate limit, or both the provider and the local fallback failed.
        """
        if decision is None:
            raise DecisionRefused(
                "kilo inference needs the caller's decision; it is not something "
                "the Body grants itself"
            )
        definition = get_agent(agent)
        messages = self._messages(definition, prompt, system)
        try:
            text = await self._cloud(definition, messages, timeout)
            return InferenceResult(text=text[:MAX_REPLY], provider=PRIMARY_PROVIDER,
                                   model=definition.model)
        except RateLimitedError as exc:
            reason = f"{PRIMARY_PROVIDER} rate-limited: {exc}"
        except NotConfiguredError as exc:
            reason = f"{PRIMARY_PROVIDER} has no credential: {exc}"
        except ConnectorError as exc:
            raise ModelUnavailable(
                f"{PRIMARY_PROVIDER} failed and this is not a rate limit, so the "
                f"local model is not substituted for it: {exc}"
            ) from exc

        self._log("kilo_model_fallback", agent=agent, reason=reason)
        return InferenceResult(
            text=await self._local(definition, messages), provider="ollama",
            model=definition.fallback, fell_back=True, reason=reason,
        )

    # -- internals -------------------------------------------------------

    async def _cloud(
        self, definition: Agent, messages: List[Dict[str, str]], timeout: Optional[int]
    ) -> str:
        """Send one request to the primary cloud provider.

        Args:
            definition: The agent being answered.
            messages: The prompt.
            timeout: Cloud timeout override.

        Returns:
            The reply text.

        Raises:
            RateLimitedError: The provider's quota is exhausted.
            NotConfiguredError: No credential is available.
            ConnectorError: Any other provider failure.
        """
        connector = self.bridge.connectors.get(PRIMARY_PROVIDER)
        if connector is None or not connector.is_configured():
            raise NotConfiguredError(f"{PRIMARY_PROVIDER} is not configured")
        return await connector.chat(messages, definition.model, timeout or CLOUD_TIMEOUT)

    async def _local(self, definition: Agent, messages: List[Dict[str, str]]) -> str:
        """Answer from a local Ollama model.

        Args:
            definition: The agent being answered, for its fallback model name.
            messages: The prompt.

        Returns:
            The reply text.

        Raises:
            ModelUnavailable: Ollama is unreachable or returned nothing usable.
        """
        try:
            return await call_local(definition.fallback, messages, 2)
        except BackendError as exc:
            raise ModelUnavailable(
                f"{PRIMARY_PROVIDER} would not answer and the local fallback "
                f"{definition.fallback} did not either: {exc}"
            ) from exc

    def _messages(self, definition: Agent, prompt: str, system: str) -> List[Dict[str, str]]:
        """Build the chat messages for one agent call.

        Args:
            definition: The agent being answered.
            prompt: The user message.
            system: Extra system text.
        """
        instructions = definition.system if not system else f"{definition.system}\n\n{system}"
        return [
            {"role": "system", "content": instructions},
            {"role": "user", "content": str(prompt)[:MAX_PROMPT]},
        ]

    def _log(self, action: str, **fields: Any) -> None:
        """Record a routing event in the activity log, when wired."""
        if self.logger is None:
            return
        try:
            self.logger.log("kilo", action, **fields)
        except Exception:
            pass


_CONFIG: Optional[KiloConfig] = None


def get_config(logger: Optional[Any] = None) -> KiloConfig:
    """Return the process-wide routing config, building it on first use.

    Args:
        logger: Activity logger, used the first time only.
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = KiloConfig(logger=logger)
    return _CONFIG
