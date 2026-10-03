"""Shared mocks for the Corpus Callosum tests.

Provides the `httpx.MockTransport` patch used to stand in for every cloud
provider, plus small helpers for building canned responses.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List

import httpx

MESSAGES: List[Dict[str, str]] = [{"role": "user", "content": "hello"}]

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    "data: [DONE]\n\n"
)

# Captured once at import. Reading `httpx.AsyncClient.__init__` inside the
# context manager would capture a previously patched version when a test nests
# two mock_http blocks, and the first handler would silently win.
_REAL_ASYNC_CLIENT_INIT = httpx.AsyncClient.__init__


@contextmanager
def mock_http(
    monkeypatch, handler: Callable[[httpx.Request], httpx.Response]
) -> Iterator[None]:
    """Route every ``httpx.AsyncClient`` call to a mock handler.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        handler: Callable receiving an ``httpx.Request`` and returning a response.

    Yields:
        Nothing; the patching is undone when the block exits.
    """

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        _REAL_ASYNC_CLIENT_INIT(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    yield


def openai_reply(text: str) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler answering with an OpenAI-style chat completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return handler


def clear_all_keys(monkeypatch, connector_classes) -> None:
    """Remove every provider credential from the environment.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        connector_classes: The connector classes whose key names to clear.
    """
    for cls in connector_classes:
        for name in (cls.API_KEY_ENV, *cls.API_KEY_ALIASES):
            monkeypatch.delenv(name, raising=False)
