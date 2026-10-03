"""Corpus Callosum — Colab Connector.

Talks to the Flask server running on Jayanthan's Google Colab LLaMA 3.1 8B
notebook, reached through an ngrok tunnel. Two things make this provider
different from the rest:

* No API key — ngrok's URL *is* the credential, so it is treated as the key.
* The URL changes every time the Colab session restarts, and Colab is slow to
  wake up, so the timeout is generous and a stale URL is reported clearly
  instead of raising a generic connection error.

How the notebook answers is defined in :mod:`brain.corpus_callosum.colab_notebook`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.corpus_callosum.base_connector import BaseConnector
from brain.corpus_callosum.colab_notebook import ColabNotebookProtocol
from core.config import env

# Colab's free T4 needs time to load weights on the first request.
COLD_START_TIMEOUT = 180


class ColabConnector(ColabNotebookProtocol, BaseConnector):
    """Connects to the Colab cloud brain over its ngrok tunnel.

    Attributes:
        url_changed_at: Timestamp of the last observed ngrok URL change.
    """

    NAME = "colab"
    BASE_URL = ""
    DEFAULT_MODEL = "llama3.1:8b"
    API_KEY_ENV = "COLAB_API_URL"
    API_KEY_ALIASES = ("COLAB_URL", "NGROK_URL")
    REQUESTS_PER_MINUTE = 0
    TIMEOUT_SECONDS = COLD_START_TIMEOUT
    OPENAI_COMPATIBLE = False
    PUBLISHES_CATALOGUE = False

    def __init__(self) -> None:
        """Initialise the connector and record the current tunnel URL."""
        super().__init__()
        self.url_changed_at = 0.0
        self.notebook_report: Dict[str, Any] = {}

    def base_url(self) -> str:
        """Return the current ngrok URL without a trailing slash.

        Returns:
            The tunnel base URL, or an empty string when unset.
        """
        return self.api_key.rstrip("/")

    def health_url(self) -> str:
        """Return the notebook's health endpoint."""
        return f"{self.base_url()}/health"

    def _chat_url(self, model: str) -> str:
        """Return the notebook's chat endpoint.

        Args:
            model: Ignored; the notebook picks its own loaded model.

        Returns:
            Absolute URL of ``/chat``.
        """
        return f"{self.base_url()}/chat"

    def headers(self) -> Dict[str, str]:
        """Return headers without bearer auth — ngrok needs none."""
        return {"Content-Type": "application/json"}

    def model_for(self, model: Optional[str] = None) -> str:
        """Resolve the model, honouring ``COLAB_MODEL`` from the environment.

        Args:
            model: Caller-supplied model, or None.

        Returns:
            The model name sent to the notebook.
        """
        return model or env("COLAB_MODEL", self.DEFAULT_MODEL)

    def _payload(self, messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
        """Return the notebook's expected request body.

        Args:
            messages: Chat messages including the system prompt.
            model: Model name for the notebook.

        Returns:
            JSON body with ``model`` and ``messages``.
        """
        return {"model": model, "messages": messages, "stream": False}

    async def list_models(self) -> List[Dict[str, Any]]:
        """The notebook does not publish a model catalogue.

        Returns:
            An empty list, so the model check reports this provider as
            unverifiable rather than wrong.
        """
        return []

    async def health_check(self) -> bool:
        """Probe the notebook's ``/health`` endpoint.

        The notebook reports more than a status code — Flask up, the Ollama
        process alive, the upstream API reachable — so the response is kept for
        the dashboard instead of being reduced to a boolean.

        Returns:
            True when the Colab brain answers.
        """
        if not self.is_configured():
            return False
        import httpx

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(self.health_url())
            if response.status_code >= 400:
                self.record_failure(self._failure_detail(response))
                return False
            self.notebook_report = self.parse_report(response.text)
            if not self.notebook_report:
                self.record_failure(self._failure_detail(response))
                return False
            overall = str(self.notebook_report.get("overall", "healthy"))
            if overall != "healthy":
                self.record_failure(f"notebook reports {overall}")
                return False
            return True
        except httpx.HTTPError as exc:
            hint = "check that the Colab notebook is running and .env has the current ngrok URL"
            self.record_failure(f"{exc} ({hint})")
            return False

    @staticmethod
    def _failure_detail(response: Any) -> str:
        """Explain a failed probe, distinguishing ngrok from the notebook.

        A dead Colab runtime does not return a clean error: the ngrok tunnel is
        still registered, so it answers with its own HTML error page and a 404.
        Reporting that as a bare ``HTTP 404`` sends the user hunting for a wrong
        URL when the URL is in fact correct and the notebook is simply stopped.

        Args:
            response: The httpx response from the probe.

        Returns:
            A message naming the likely cause.
        """
        body = response.text.lstrip()[:200].lower()
        if body.startswith("<!doctype") or "<html" in body:
            return (
                f"HTTP {response.status_code} from ngrok, not the notebook: "
                "the Colab runtime is stopped or restarting; the URL is unchanged"
            )
        return f"HTTP {response.status_code}"