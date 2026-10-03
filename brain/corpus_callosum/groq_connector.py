"""Corpus Callosum — Groq Connector.

Groq's free tier serves LLaMA 70B at very high speed and is ZUZZ's default
cloud brain. Speaks the OpenAI chat schema, so only the endpoint, key and
quota differ from the base connector.
"""

from __future__ import annotations

from typing import Dict, List

from brain.corpus_callosum.base_connector import BaseConnector
from core.config import env


class GroqConnector(BaseConnector):
    """Dedicated Groq API wrapper.

    Free tier allows roughly 30 requests per minute; the connector tracks that
    locally so the Thalamus can fall back instead of collecting HTTP 429s.
    """

    NAME = "groq"
    BASE_URL = "https://api.groq.com/openai/v1"
    # LLaMA 3.3 70B was retired from Groq's catalogue. gpt-oss-120b is the
    # largest general model they serve, and keeps the "complex tasks go to a
    # big cloud model" rule in PROJECT.md section 9.
    DEFAULT_MODEL = "openai/gpt-oss-120b"
    API_KEY_ENV = "GROQ_API_KEY"
    API_KEY_ALIASES = ("GROQ_KEY",)
    REQUESTS_PER_MINUTE = 30
    TIMEOUT_SECONDS = 60

    def model_for(self, model: str | None = None) -> str:
        """Resolve the model, honouring ``GROQ_MODEL`` from the environment.

        Args:
            model: Caller-supplied model, or None.

        Returns:
            The Groq model identifier.
        """
        return model or env("GROQ_MODEL", self.DEFAULT_MODEL)

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, object]:
        """Return the Groq request body.

        Args:
            messages: Chat messages including the system prompt.
            model: Resolved model identifier.

        Returns:
            JSON body with Groq's supported sampling parameters.
        """
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 2048,
        }
