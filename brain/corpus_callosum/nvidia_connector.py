"""Corpus Callosum — NVIDIA Build Connector.

NVIDIA's inference catalogue is OpenAI-compatible at
``integrate.api.nvidia.com`` and free with an API key. Good fit for the
medium-to-large open models that sit between local 3B and Groq 70B.
"""

from __future__ import annotations

from typing import Dict, List

from brain.corpus_callosum.base_connector import BaseConnector
from core.config import env


class NvidiaConnector(BaseConnector):
    """Dedicated NVIDIA Build API wrapper."""

    NAME = "nvidia"
    BASE_URL = "https://integrate.api.nvidia.com/v1"
    DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct"
    API_KEY_ENV = "NVIDIA_API_KEY"
    API_KEY_ALIASES = ("NVIDIA_KEY", "NVAPI_KEY")
    REQUESTS_PER_MINUTE = 40
    TIMEOUT_SECONDS = 90

    def model_for(self, model: str | None = None) -> str:
        """Resolve the model, honouring ``NVIDIA_MODEL`` from the environment.

        Args:
            model: Caller-supplied model, or None.

        Returns:
            The NVIDIA catalogue model identifier.
        """
        return model or env("NVIDIA_MODEL", self.DEFAULT_MODEL)

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, object]:
        """Return the NVIDIA request body.

        Args:
            messages: Chat messages including the system prompt.
            model: Resolved model identifier.

        Returns:
            JSON body with NVIDIA's recommended sampling settings.
        """
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.6,
            "top_p": 0.9,
            "max_tokens": 2048,
        }
