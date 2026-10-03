"""Corpus Callosum — Connector Transport.

The HTTP half of a connector: non-streaming `chat` and streaming `stream_chat`,
plus request construction hooks. Health and quota reporting live in
`connector_status.py`.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, List, Optional

from brain.corpus_callosum.connector_state import ConnectorError, RateLimitedError


class ConnectorTransport:
    """HTTP transport mixin for connectors.

    Expects the host class to provide ``_chat_url``, ``_payload``,
    ``extract_text``, ``parse_chunk``, ``headers``, ``model_for``,
    ``check_quota``, ``record_success``, ``record_failure`` and the class
    attributes defined in ``BaseConnector``.
    """

    REQUESTS_PER_MINUTE = 0
    TIMEOUT_SECONDS = 60

    def _timeout(self, override: Optional[int]) -> float:
        """Resolve the request timeout in seconds."""
        return float(override or self.TIMEOUT_SECONDS)

    def _check_response(self, status_code: int, body: str) -> None:
        """Translate an HTTP error status into the right exception.

        Args:
            status_code: The response status code.
            body: Response body, truncated for the message.

        Raises:
            RateLimitedError: On HTTP 429.
            ConnectorError: On any other 4xx or 5xx.
        """
        name = self.NAME  # type: ignore[attr-defined]
        if status_code == 429:
            self.record_failure("rate limited")  # type: ignore[attr-defined]
            self.rate_limit.bench()  # type: ignore[attr-defined]
            raise RateLimitedError(f"{name}: HTTP 429 rate limited")
        if status_code >= 400:
            self.record_failure(f"HTTP {status_code}")  # type: ignore[attr-defined]
            raise ConnectorError(f"{name}: HTTP {status_code}: {body[:200]}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Send a chat request and return the full reply.

        Args:
            messages: Chat messages including the system prompt.
            model: Optional model override.
            timeout: Optional timeout in seconds.

        Returns:
            The assistant's reply.

        Raises:
            NotConfiguredError: If no credential is set.
            RateLimitedError: If the quota is exhausted.
            ConnectorError: If the provider fails or returns nothing usable.
        """
        import httpx

        self.check_quota()  # type: ignore[attr-defined]
        chosen = self.model_for(model)  # type: ignore[attr-defined]
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout(timeout)) as client:
                response = await client.post(
                    self._chat_url(chosen),  # type: ignore[attr-defined]
                    json=self._payload(messages, chosen),  # type: ignore[attr-defined]
                    headers=self.headers(),  # type: ignore[attr-defined]
                )
                self._check_response(response.status_code, response.text)
                payload = response.json()
        except ConnectorError:
            raise
        except httpx.HTTPError as exc:
            self.record_failure(str(exc))  # type: ignore[attr-defined]
            raise ConnectorError(f"{self.NAME}: request failed: {exc}") from exc  # type: ignore[attr-defined]
        except ValueError as exc:
            self.record_failure(f"invalid JSON: {exc}")  # type: ignore[attr-defined]
            raise ConnectorError(f"{self.NAME}: invalid JSON response") from exc  # type: ignore[attr-defined]

        text = self.extract_text(payload)  # type: ignore[attr-defined]
        if not text.strip():
            self.record_failure("empty response")  # type: ignore[attr-defined]
            raise ConnectorError(f"{self.NAME}: returned an empty response")  # type: ignore[attr-defined]
        self.record_success((time.perf_counter() - started) * 1000)  # type: ignore[attr-defined]
        return text

    async def list_models(self) -> List[str]:
        """Return the model identifiers this provider currently serves.

        Providers that speak the OpenAI schema expose ``/models`` returning
        ``{"data": [{"id": ...}]}``.

        A provider that cannot be reached raises ``ConnectorError`` rather than
        returning an empty list. The two cases mean different things to the
        model check: an unreachable catalogue says nothing about the model,
        while an empty catalogue means the model really is gone.

        Returns:
            Model identifiers, possibly empty when the provider serves none.

        Raises:
            ConnectorError: When the catalogue cannot be fetched.
        """
        if not self.is_configured():  # type: ignore[attr-defined]
            return []
        import httpx

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{self.BASE_URL.rstrip('/')}/models", headers=self.headers()  # type: ignore[attr-defined]
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.NAME}: catalogue unreachable: {exc}") from exc  # type: ignore[attr-defined]
        if response.status_code >= 400:
            raise ConnectorError(  # type: ignore[attr-defined]
                f"{self.NAME}: catalogue request failed: HTTP {response.status_code}"  # type: ignore[attr-defined]
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(f"{self.NAME}: invalid catalogue JSON") from exc  # type: ignore[attr-defined]

        entries = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []
        return [
            str(entry["id"])
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        ]

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream a reply token by token.

        Args:
            messages: Chat messages including the system prompt.
            model: Optional model override.
            timeout: Optional timeout in seconds.

        Yields:
            Text fragments as the provider emits them.

        Raises:
            NotConfiguredError: If no credential is set.
            RateLimitedError: If the quota is exhausted.
            ConnectorError: If the provider fails mid-stream.
        """
        import httpx

        self.check_quota()  # type: ignore[attr-defined]
        chosen = self.model_for(model)  # type: ignore[attr-defined]
        payload = dict(self._payload(messages, chosen))  # type: ignore[attr-defined]
        payload["stream"] = True
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout(timeout)) as client:
                async with client.stream(
                    "POST",
                    self._chat_url(chosen),  # type: ignore[attr-defined]
                    json=payload,
                    headers=self.headers(),  # type: ignore[attr-defined]
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        self._check_response(response.status_code, body)
                    async for line in response.aiter_lines():
                        fragment = self.parse_chunk(line)  # type: ignore[attr-defined]
                        if fragment:
                            yield fragment
        except ConnectorError:
            raise
        except httpx.HTTPError as exc:
            self.record_failure(str(exc))  # type: ignore[attr-defined]
            raise ConnectorError(f"{self.NAME}: stream failed: {exc}") from exc  # type: ignore[attr-defined]
        self.record_success((time.perf_counter() - started) * 1000)  # type: ignore[attr-defined]
