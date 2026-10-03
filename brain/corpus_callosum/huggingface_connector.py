"""Corpus Callosum — HuggingFace Connector.

The HuggingFace inference API is not OpenAI-compatible: it takes a flat
``inputs`` string rather than a message list and returns
``[{"generated_text": ...}]``. This connector adapts both directions, and
flattens the conversation so the model still sees the system prompt.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.corpus_callosum.base_connector import BaseConnector
from brain.corpus_callosum.connector_stream import parse_hf_line
from core.config import env

DEFAULT_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"


class HuggingFaceConnector(BaseConnector):
    """Dedicated HuggingFace Inference API wrapper."""

    NAME = "huggingface"
    BASE_URL = "https://api-inference.huggingface.co/models"
    DEFAULT_MODEL = DEFAULT_MODEL
    API_KEY_ENV = "HUGGINGFACE_API_KEY"
    API_KEY_ALIASES = ("HF_API_KEY", "HUGGINGFACE_TOKEN")
    REQUESTS_PER_MINUTE = 20
    TIMEOUT_SECONDS = 120

    def model_for(self, model: Optional[str] = None) -> str:
        """Resolve the model, honouring ``HUGGINGFACE_MODEL`` from the environment.

        Args:
            model: Caller-supplied model, or None.

        Returns:
            The HuggingFace repo id.
        """
        return model or env("HUGGINGFACE_MODEL", env("HF_MODEL", DEFAULT_MODEL))

    def _chat_url(self, model: str) -> str:
        """Return the model-specific inference endpoint.

        Args:
            model: HuggingFace repo id.

        Returns:
            Absolute URL including the model name.
        """
        return f"{self.BASE_URL.rstrip('/')}/{model}"

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
        """Flatten the conversation into the inference API's schema.

        Args:
            messages: Chat messages including the system prompt.
            model: Resolved repo id, unused by this provider's payload.

        Returns:
            JSON body with ``inputs``, ``parameters`` and streaming flags.
        """
        return {
            "inputs": self.flatten(messages),
            "parameters": {"max_new_tokens": 1024, "temperature": 0.7, "return_full_text": False},
            "stream": False,
        }

    async def list_models(self) -> List[str]:
        """Return the catalogue of models this key can reach.

        HuggingFace has no single account-wide model list, so the configured
        repo is checked directly: a 200 means the repo exists, a 404 means it was
        renamed or removed. The request is authenticated, because the endpoint
        answers 401 for a missing repo rather than 404, and redirects are
        followed so a renamed repo still resolves.

        Returns:
            A single-item list when the repo exists, empty when it is gone.

        Raises:
            ConnectorError: When HuggingFace cannot be reached. A gated repo
                answers 401 or 403, which is a permission problem rather than
                proof the model was removed.
        """
        if not self.is_configured():
            return []
        import httpx

        from brain.corpus_callosum.connector_state import ConnectorError

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(
                    f"https://huggingface.co/api/models/{self.model_for()}",
                    headers=self.headers(),
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.NAME}: repo check failed: {exc}") from exc
        if response.status_code == 200:
            return [self.model_for()]
        if response.status_code == 404:
            return []
        raise ConnectorError(
            f"{self.NAME}: repo check returned HTTP {response.status_code}"
            + (" (repo may be gated)" if response.status_code in (401, 403) else "")
        )

    def flatten(self, messages: List[Dict[str, str]]) -> str:
        """Render a message list as a single prompt string.

        Args:
            messages: Chat messages including the system prompt.

        Returns:
            A ``Role: text`` transcript.
        """
        labels = {"system": "System", "assistant": "Assistant", "user": "User"}
        return "\n\n".join(
            f"{labels.get(m.get('role', 'user'), 'User')}: {m.get('content', '')}"
            for m in messages
        )

    def extract_text(self, payload: Dict[str, Any]) -> str:
        """Read HuggingFace's list-of-objects response.

        Args:
            payload: Decoded JSON response.

        Returns:
            The generated text, or an empty string.
        """
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                return str(first.get("generated_text") or "")
            return str(first)
        if isinstance(payload, dict):
            return str(payload.get("generated_text") or payload.get("error") or "")
        return ""

    def parse_chunk(self, line: str) -> str:
        """Parse HuggingFace's event-stream format.

        Args:
            line: Raw ``data:`` line from the stream.

        Returns:
            The generated fragment, or an empty string.
        """
        return parse_hf_line(line)
