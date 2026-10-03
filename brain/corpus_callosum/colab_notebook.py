"""Corpus Callosum — Colab Notebook Protocol.

The Colab connector does not speak the OpenAI schema, so the parts of it that
describe *the notebook's* wire format live here rather than in the connector.
Splitting them out keeps each file readable: the connector owns HTTP and
routing, this module owns how the Flask server answers.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# Response keys the Flask server has used across sessions, in priority order.
REPLY_KEYS = ("response", "reply", "answer", "message", "content", "text", "output")


class ColabNotebookProtocol:
    """Response parsing for the Colab Flask server.

    Mixed into :class:`~brain.corpus_callosum.colab_connector.ColabConnector`,
    which supplies ``notebook_report``.
    """

    notebook_report: Dict[str, Any]

    def extract_text(self, payload: Dict[str, Any]) -> str:
        """Find the reply in the notebook's response.

        The Flask server has returned the answer under several keys depending
        on the notebook version, so every known key is tried.

        Args:
            payload: Decoded JSON response.

        Returns:
            The generated text, or an empty string.
        """
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ""
        for key in REPLY_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return str(message.get("content") or "")
        message = payload.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return ""

    @staticmethod
    def parse_report(body: str) -> Dict[str, Any]:
        """Parse the notebook's health JSON.

        Args:
            body: Raw response body.

        Returns:
            The decoded report, or an empty dict when it is not JSON.
        """
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def health_detail(self) -> str:
        """Return a one-line summary of the notebook's own health report.

        Returns:
            For example ``flask running, ollama alive``, or ``ok``.
        """
        report = self.notebook_report
        if not report:
            return "ok"
        parts: List[str] = []
        for key in ("flask", "ollama_process", "ollama_api"):
            if key in report:
                parts.append(f"{key.replace('_', ' ')} {report[key]}")
        return ", ".join(parts) or "ok"