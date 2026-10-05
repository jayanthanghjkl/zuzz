"""Occipital Lobe — the transport seam to Ollama.

Vision talks to Moondream over plain HTTP, so the transport is an interface with
two implementations and nothing else in the region knows which one it has. That
matters for three reasons: the whole region can be tested without a GPU or an
Ollama process, a dry-run preview can render a plausible answer without a model,
and swapping Ollama for another local runtime is one class rather than a sweep
through six modules.

:class:`ScriptedTransport` is in production code for the reason
:class:`body.hands.recorder.RecordingActuator` is: a preview needs the same stand-in
a test does. It answers from a script rather than from a model, so it is
deterministic — the same prompt always produces the same bytes — which is what makes
a test about *this* code distinguishable from a test about Moondream's mood today.

Errors are normalised at this boundary. Anything that goes wrong on the wire becomes
:class:`~brain.occipital_lobe.exceptions.VisionModelUnavailable` with a reason, so
the caller never has to catch ``httpx.HTTPError`` and JSON errors side by side, and
so "Ollama is not running" never surfaces as a 500.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from brain.occipital_lobe.exceptions import VisionModelUnavailable, VisionTimeout

#: Ollama's own address. The Cerebrum reads the same variable through
#: ``core.config``, so a machine configured in one place is configured in both.
DEFAULT_HOST = "http://localhost:11434"

#: Vision is a small model on a local runtime; a minute is generous and a shorter
#: wait is friendlier than a hung request.
DEFAULT_TIMEOUT = 45.0

#: Ollama's documented ceiling for base64 images. A 4K screen photo is well
#: under it; the check exists because the alternative is an opaque 400.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class VisionTransport:
    """The interface the vision client posts through."""

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send one request and return the decoded JSON body.

        Args:
            path: The endpoint path, e.g. ``/api/generate``.
            payload: The JSON body.

        Returns:
            The decoded response.

        Raises:
            VisionModelUnavailable: The endpoint could not be reached.
            VisionTimeout: It did not answer in time.
        """
        raise NotImplementedError


class OllamaTransport(VisionTransport):
    """Posts to a local Ollama over HTTP.

    Attributes:
        host: Base URL, without a trailing slash.
        timeout: Seconds to wait.
    """

    def __init__(self, host: str = DEFAULT_HOST, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Configure the transport.

        Args:
            host: Base URL of the Ollama server.
            timeout: Seconds to wait for a reply.
        """
        self.host = host.rstrip("/")
        self.timeout = timeout

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send one request to Ollama.

        Args:
            path: The endpoint path.
            payload: The JSON body.

        Returns:
            The decoded response body.

        Raises:
            VisionModelUnavailable: Ollama is not reachable, or answered with
                something that is not JSON.
            VisionTimeout: Ollama did not answer within ``timeout``.
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.host}{path}", json=payload)
                response.raise_for_status()
                return dict(response.json())
        except httpx.TimeoutException as exc:
            raise VisionTimeout(
                f"Ollama did not answer within {self.timeout:g}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VisionModelUnavailable(
                f"Ollama at {self.host} is not answering: {exc}", reason="transport"
            ) from exc
        except ValueError as exc:
            raise VisionModelUnavailable(
                f"Ollama at {self.host} returned invalid JSON: {exc}", reason="bad_response"
            ) from exc


class ScriptedTransport(VisionTransport):
    """Answers from a script, deterministically.

    Used by the dry-run preview and by the tests. Deliberately not a mock framework
    object: it is the same class in both, so a preview exercises the real parsing
    and threshold code paths and cannot drift away from what the tests cover.

    Attributes:
        replies: Canned replies, consumed in order. The last one repeats once the
            script runs out, so a preview that asks three questions is not refused
            on the fourth.
        calls: Every payload this transport was handed, for assertions.
    """

    def __init__(self, replies: Optional[Sequence[str]] = None) -> None:
        """Build the transport.

        Args:
            replies: Canned reply strings. Defaults to one honest "no answer"
                reply, which is what an unconfigured preview should produce rather
                than a fabricated success.
        """
        self._replies: List[str] = list(replies) if replies else [""]
        self.calls: List[Dict[str, Any]] = []

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the next scripted reply.

        Args:
            path: Ignored; recorded in :attr:`calls`.
            payload: Recorded in :attr:`calls`.

        Returns:
            A reply shaped like Ollama's ``/api/generate`` response.
        """
        self.calls.append({"path": path, "payload": json.loads(json.dumps(payload, default=str))})
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return {"model": payload.get("model", ""), "response": self._replies[index], "done": True}

    def prompt_for(self, index: int = 0) -> str:
        """Return the prompt from one recorded call.

        Args:
            index: Which recorded call to read.

        Returns:
            The prompt text, or an empty string when there was no such call.
        """
        if index >= len(self.calls):
            return ""
        return str(self.calls[index]["payload"].get("prompt", ""))