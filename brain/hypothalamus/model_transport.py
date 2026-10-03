"""Hypothalamus — model transport and swap accounting.

The two things the model manager needs that are not policy: a way to actually
reach Ollama, and a way to notice when it is being used oddly.

Separated from :mod:`brain.hypothalamus.model_manager` for the same reason the
Hands layer separates its actuator. The manager holds the rules — the cap, the
gate, the rollback — and those rules are worth testing exhaustively. Folding the
HTTP client into the same module means the budget tests either need a running
Ollama or need a stub of the very code under test. Here, the boundary is a
three-method protocol, so a test supplies a recorder and exercises the policy
without a network, a GPU, or an Ollama install.
"""

from __future__ import annotations

import time
from typing import Any, List, Protocol

from brain.hypothalamus.exceptions import ModelLoadError, ModelUnloadError

#: How many model swaps in :data:`SWAP_WINDOW_SECONDS` the Amygdala treats as a
#: pattern worth scoring rather than normal operation.
SWAP_BURST_THRESHOLD = 10

#: Length of the sliding window the burst count is taken over.
SWAP_WINDOW_SECONDS = 60


class ModelTransport(Protocol):
    """The subset of the Ollama API the manager depends on.

    Declared as a protocol rather than an abstract base class so a test double is
    a plain object with three methods, and so nothing has to import Ollama's
    vocabulary to satisfy it.
    """

    async def load(self, model: str, timeout: float) -> None:
        """Load ``model`` into VRAM, raising on failure or timeout."""

    async def unload(self, model: str, timeout: float) -> None:
        """Unload ``model`` from VRAM, raising on failure."""

    async def resident(self) -> List[str]:
        """Return the models currently held in VRAM."""


class OllamaTransport:
    """Talks to a real Ollama server over HTTP.

    Ollama has no dedicated load or unload endpoint: a model is made resident by
    sending it a request, and evicted by sending the same request with
    ``keep_alive`` 0. That is why both methods post to ``/api/generate``.
    """

    def __init__(self, host: str = "") -> None:
        """Bind to an Ollama host.

        Args:
            host: Base URL. Empty reads ``OLLAMA_HOST`` at call time, so editing
                the environment still takes effect without a restart.
        """
        self._host = host

    def _url(self, path: str) -> str:
        """Return the absolute URL for an Ollama endpoint."""
        if not self._host:
            from brain.cerebrum.reasoning import ollama_host

            return f"{ollama_host()}{path}"
        return f"{self._host.rstrip('/')}{path}"

    async def load(self, model: str, timeout: float) -> None:
        """Make a model resident by generating a single token from it.

        Args:
            model: Ollama model name.
            timeout: Seconds to wait. The manager sets this from whether the
                weights are likely to be in page cache.

        Raises:
            ModelLoadError: Ollama refused, timed out, or is not running. The
                timeout message suggests the two things that actually help —
                a smaller model, or the cloud — because "it timed out" on its own
                leaves the caller with nothing to do.
        """
        import httpx

        payload = {"model": model, "prompt": "", "stream": False}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self._url("/api/generate"), json=payload)
        except httpx.TimeoutException as exc:
            raise ModelLoadError(
                f"loading {model} exceeded {timeout:g}s. Use a smaller model, or route "
                "this task to the cloud."
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelLoadError(
                f"Ollama is not reachable, so {model} could not be loaded: {exc}"
            ) from exc
        if response.status_code != 200:
            raise ModelLoadError(
                f"Ollama refused to load {model}: HTTP {response.status_code}"
            )

    async def unload(self, model: str, timeout: float) -> None:
        """Evict a model by sending an empty generate with ``keep_alive`` 0.

        Args:
            model: Ollama model name.
            timeout: Seconds to wait.

        Raises:
            ModelUnloadError: Ollama refused or is not running.
        """
        import httpx

        payload = {"model": model, "keep_alive": 0}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self._url("/api/generate"), json=payload)
        except httpx.HTTPError as exc:
            raise ModelUnloadError(f"could not unload {model}: {exc}") from exc
        if response.status_code != 200:
            raise ModelUnloadError(
                f"Ollama refused to unload {model}: HTTP {response.status_code}"
            )

    async def resident(self) -> List[str]:
        """Return the models Ollama reports as loaded.

        Returns an empty list rather than raising when Ollama is unreachable: this
        is a status read, and the caller already knows the authoritative answer
        from its own accounting.
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self._url("/api/ps"))
            payload = response.json()
        except Exception:
            return []
        return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]


class SwapWatch:
    """Counts model swaps in a sliding window so the Amygdala can score them.

    Ten swaps in a minute is normal operation — a long conversation moving
    between a fast and a complex model does exactly that. Twenty is not, and the
    difference between the two is worth a threat assessment, because a loop that
    keeps evicting and reloading models is also what an agent that has lost track
    of its own state looks like.
    """

    def __init__(
        self,
        threshold: int = SWAP_BURST_THRESHOLD,
        window: float = SWAP_WINDOW_SECONDS,
    ) -> None:
        """Configure the burst detector.

        Args:
            threshold: Swaps in ``window`` that constitute a burst.
            window: Length of the sliding window in seconds.
        """
        self.threshold = threshold
        self.window = window
        self._events: List[float] = []

    def record(self) -> int:
        """Record one swap and return the count now inside the window."""
        now = time.time()
        self._events.append(now)
        self._trim(now)
        return len(self._events)

    def count(self) -> int:
        """Return the swaps inside the window without recording a new one."""
        self._trim(time.time())
        return len(self._events)

    def is_suspicious(self) -> bool:
        """Return True when the swap rate looks like a loop rather than work.

        Strictly greater than the threshold: ten swaps in a minute is a busy but
        ordinary conversation, and flagging it would train the operator to ignore
        the flag.
        """
        return self.count() > self.threshold

    def _trim(self, now: float) -> None:
        """Drop events that have aged out of the window."""
        self._events = [t for t in self._events if now - t < self.window]
