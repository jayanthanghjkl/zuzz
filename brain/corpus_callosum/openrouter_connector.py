"""Corpus Callosum — OpenRouter Connector.

OpenRouter proxies many providers behind one OpenAI-compatible endpoint. ZUZZ
prefers its free-tier models so the "runs 100% free" rule in PROJECT.md
section 1 holds; a keyed paid model can still be forced through ``.env``.
"""

from __future__ import annotations

from typing import Dict, List

from brain.corpus_callosum.base_connector import BaseConnector
from core.config import env, get_personality

DEFAULT_FREE_MODEL = "qwen/qwen3.8-27b:free"


class OpenRouterConnector(BaseConnector):
    """Dedicated OpenRouter API wrapper."""

    NAME = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = DEFAULT_FREE_MODEL
    API_KEY_ENV = "OPENROUTER_API_KEY"
    API_KEY_ALIASES = ("OPENROUTER_KEY",)
    REQUESTS_PER_MINUTE = 20
    TIMEOUT_SECONDS = 90

    def extra_headers(self) -> Dict[str, str]:
        """Return the attribution headers OpenRouter asks integrators to send.

        Returns:
            Referer and title headers identifying ZUZZ.
        """
        owner = get_personality().get("owner", "Jayanthan")
        return {"HTTP-Referer": "http://localhost:8080", "X-Title": f"ZUZZ ({owner})"}

    def model_for(self, model: str | None = None) -> str:
        """Resolve the model, honouring ``OPENROUTER_MODEL`` from the environment.

        Args:
            model: Caller-supplied model, or None.

        Returns:
            The OpenRouter model identifier.
        """
        return model or env("OPENROUTER_MODEL", DEFAULT_FREE_MODEL)

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, object]:
        """Return the OpenRouter request body.

        Args:
            messages: Chat messages including the system prompt.
            model: Resolved model identifier.

        Returns:
            JSON body with routing hints so free models are preferred.
        """
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 2048,
            "provider": {"sort": "latency", "allow_fallbacks": True},
        }
