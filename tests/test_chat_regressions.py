"""Regression tests for the real router, streaming engine and gated action runner."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain.cerebrum.cerebrum import Cerebrum, CerebrumError
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.spinal_cord.kill_switch import get_kill_switch
from brain.thalamus.router import Router
from body.fingers import FileManager, DirectoryManager
from face.api import routes_chat


@pytest.fixture
def chat_app(monkeypatch):
    """Wire real components; substitute only the external model transport."""
    app = FastAPI()
    switch = get_kill_switch()
    engine = Cerebrum(gate=PermissionGate(kill_switch=switch), trust_level=3, enable_enhancement=False)
    monkeypatch.setattr(engine.bridge, "configured_providers", lambda: [])
    state = app.state
    state.thalamus = Router(cerebrum=engine)
    state.kill_switch, state.trust = switch, SimpleNamespace(current=lambda: 3)
    state.file_manager, state.directory_manager = FileManager(), DirectoryManager()
    state.hands, state.hypothalamus, state.logger = None, None, Mock()
    app.include_router(routes_chat.router)
    async def online():
        return True
    monkeypatch.setattr(routes_chat, "internet_available", online)
    return app


def collect(app, payload):
    """Read exactly one completed/failed turn through the actual WebSocket API."""
    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        socket.send_json(payload)
        events = []
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["type"] in ("done", "error", "refused"):
                return events


def test_stream_reaches_real_file_write_and_completes_after_actions(chat_app, monkeypatch, tmp_path):
    """Tokens from a model become an actual file, not a success-only badge."""
    path = tmp_path / "result.txt"
    reply = 'Writing.\n```action\n' + json.dumps({"tool": "file_write", "params": {"path": str(path), "content": "real output"}}) + '\n```'
    async def transport(message, messages, level):
        yield reply
    engine = chat_app.state.thalamus.cerebrum
    monkeypatch.setattr(engine, "_stream_local", transport)
    events = collect(chat_app, {"message": "write a file"})
    assert path.read_text() == "real output"
    assert [e["type"] for e in events][-2:] == ["actions", "done"]
    assert sum(e["type"] == "done" for e in events) == 1
    assert events[-1]["prose"] == "Writing."
    assert next(e for e in events if e["type"] == "actions")["errors"] == []


async def test_router_falls_back_before_tokens(chat_app, monkeypatch):
    """Missing Ollama does not prevent a configured cloud answer."""
    engine = chat_app.state.thalamus.cerebrum
    monkeypatch.setattr(engine.bridge, "configured_providers", lambda: ["groq"])
    calls = []
    async def local(message, messages, level):
        calls.append(level)
        raise CerebrumError("Ollama not running")
        yield
    async def cloud(messages, level):
        calls.append(level)
        yield "cloud answer"
    monkeypatch.setattr(engine, "_stream_local", local)
    monkeypatch.setattr(engine, "_stream_cloud", cloud)
    events = [event async for event in chat_app.state.thalamus.route_stream("hello")]
    assert calls == [1, 2, 3]
    assert events[0] == {"type": "token", "content": "cloud answer"}
    assert events[-1]["streamed_level"] == 3
    assert events[-1]["degraded"] is True


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_websocket_honors_forced_level(chat_app, monkeypatch, level):
    """The UI model selector no longer silently streams from level one."""
    engine = chat_app.state.thalamus.cerebrum
    calls = []
    async def local(message, messages, chosen):
        calls.append(chosen)
        yield "answer"
    async def cloud(messages, chosen):
        calls.append(chosen)
        yield "answer"
    monkeypatch.setattr(engine, "_stream_local", local)
    monkeypatch.setattr(engine, "_stream_cloud", cloud)
    events = collect(chat_app, {"message": "hello", "level": level})
    assert events[-1]["type"] == "done", events
    assert calls == [level]
    assert events[-1]["streamed_level"] == level


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "x", "level": 9}, {"message": "x" * 8001}, None])
def test_invalid_websocket_payload_returns_error(chat_app, payload):
    """HTTP and WebSocket requests use the same input bounds."""
    assert collect(chat_app, payload)[-1]["type"] == "error"


def test_partial_stream_is_not_retried_or_executed(chat_app, monkeypatch, tmp_path):
    """A complete-looking action in an interrupted reply must not run."""
    path = tmp_path / "must-not-exist.txt"
    engine = chat_app.state.thalamus.cerebrum
    calls = []
    async def broken(message, messages, level):
        calls.append(level)
        yield '```action\n' + json.dumps({"tool": "file_write", "params": {"path": str(path), "content": "unsafe"}}) + '\n```'
        raise CerebrumError("connection interrupted")
    monkeypatch.setattr(engine, "_stream_local", broken)
    events = collect(chat_app, {"message": "write a file"})
    assert events[-1]["type"] == "error"
    assert not path.exists()
    assert len(calls) == 1
    assert not any(e["type"] in ("actions", "done") for e in events)


def test_stop_blocks_before_model_transport(chat_app, monkeypatch):
    """Stop remains authoritative when routing/fallback changes."""
    chat_app.state.kill_switch.trigger()
    transport = Mock(side_effect=AssertionError("Transport must not run"))
    monkeypatch.setattr(chat_app.state.thalamus.cerebrum, "_stream_local", transport)
    assert collect(chat_app, {"message": "hello"})[-1]["type"] == "refused"
    transport.assert_not_called()
