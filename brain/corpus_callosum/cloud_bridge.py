"""Corpus Callosum — Cloud Bridge.

Owns every connector and decides which one answers a cloud request. The Thalamus
asks the bridge for a provider; the bridge walks the configured fallback order
and returns the first that works, recording why any others were skipped.

This is also where LiteLLM fits: when no dedicated connector can serve a
request, the bridge falls back to a generic LiteLLM call so a provider without
a connector still works.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from brain.corpus_callosum.base_connector import BaseConnector
from brain.corpus_callosum.bridge_support import BridgeSupport
from brain.corpus_callosum.colab_connector import ColabConnector
from brain.corpus_callosum.connector_state import (
    ConnectorError,
    NotConfiguredError,
    RateLimitedError,
)
from brain.corpus_callosum.groq_connector import GroqConnector
from brain.corpus_callosum.huggingface_connector import HuggingFaceConnector
from brain.corpus_callosum.nvidia_connector import NvidiaConnector
from brain.corpus_callosum.openrouter_connector import OpenRouterConnector
from core.config import get_models

CONNECTOR_CLASSES = (
    GroqConnector,
    ColabConnector,
    HuggingFaceConnector,
    OpenRouterConnector,
    NvidiaConnector,
)

BENCH_SECONDS = 120


class CloudBridge(BridgeSupport):
    """Registry and dispatcher for cloud providers.

    Attributes:
        connectors: Provider name to connector instance.
        order: Fallback order, taken from `config/models.json`.
        _benched: Providers benched until a timestamp after a hard failure.
    """

    def __init__(self) -> None:
        """Instantiate every connector and read the configured order."""
        self.connectors: Dict[str, BaseConnector] = {
            cls.NAME: cls() for cls in CONNECTOR_CLASSES
        }
        self.order: List[str] = [
            name for name in get_models().get("fallback_order", []) if name in self.connectors
        ]
        for name, connector in self.connectors.items():
            if name not in self.order:
                self.order.append(name)
        self._benched: Dict[str, float] = {}

    def configured_providers(self) -> List[str]:
        """Return providers that have a credential available right now."""
        return [name for name in self.order if self.connectors[name].is_configured()]

    async def chat(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send a chat request to the best available connector.

        Args:
            messages: Chat messages including the system prompt.
            preferred: Provider to try first, or None to use the configured order.
            model: Optional model override.
            timeout: Optional per-attempt timeout in seconds.

        Returns:
            Dict with ``reply``, ``provider`` and ``latency_ms``.

        Raises:
            ConnectorError: If every configured provider failed.
        """
        order = self._candidate_order(preferred)
        errors: List[str] = []

        for index, name in enumerate(order):
            connector = self.connectors[name]
            try:
                text = await connector.chat(messages, model, timeout)
            except RateLimitedError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except NotConfiguredError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except ConnectorError as exc:
                errors.append(f"{name}: {exc}")
                self.bench(name)
                continue
            return {
                "reply": text,
                "provider": name,
                "model": connector.model_for(model),
                "latency_ms": round(connector.last_latency_ms or 0.0, 1),
                "attempts": index + 1,
                "errors": errors,
            }

        raise ConnectorError("All cloud providers failed - " + ("; ".join(errors) or "none configured"))

    async def stream(
        self,
        messages: List[Dict[str, str]],
        preferred: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        """Stream a reply from the best available connector.

        Args:
            messages: Chat messages including the system prompt.
            preferred: Provider to try first, or None for the configured order.
            model: Optional model override.
            timeout: Optional timeout in seconds.

        Yields:
            Dicts shaped ``{"content": str, "provider": str}``.

        Raises:
            ConnectorError: If every configured provider failed.
        """
        order = self._candidate_order(preferred)
        errors: List[str] = []

        for name in order:
            connector = self.connectors[name]
            try:
                async for fragment in connector.stream_chat(messages, model, timeout):
                    yield {"content": fragment, "provider": name}
                return
            except (RateLimitedError, NotConfiguredError, ConnectorError) as exc:
                errors.append(f"{name}: {exc}")
                self.bench(name)
                continue

        raise ConnectorError("All cloud providers failed - " + ("; ".join(errors) or "none configured"))


_bridge: Optional[CloudBridge] = None


def get_bridge() -> CloudBridge:
    """Return the process-wide cloud bridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = CloudBridge()
    return _bridge
