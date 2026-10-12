"""End-to-end tests for the Face API with the model backends stubbed out.

These exercise routing, logging and the HTTP surface without touching Ollama or
any cloud provider.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from face.api import routes_chat
from face.api.server import create_app


async def _always_online() -> bool:
    """Stand-in connectivity probe reporting a reachable cloud provider."""
    return True


async def _always_offline() -> bool:
    """Stand-in connectivity probe reporting no outbound network."""
    return False


class StubBridge:
    """Cloud bridge stand-in with no configured providers."""

    def __init__(self) -> None:
        self.connectors: Dict[str, Any] = {}

    def configured_providers(self) -> List[str]:
        """Report that no cloud provider is usable in tests."""
        return []


class StubCerebrum:
    """Cerebrum stand-in that returns canned answers."""

    def __init__(self) -> None:
        self.history: List[Dict[str, str]] = []
        self.calls: List[Dict[str, Any]] = []
        self.bridge = StubBridge()
        self.last_provider = "stub"

    async def think(self, message: str, level: int = 1, context: Any = None) -> Dict[str, Any]:
        """Return a deterministic reply for the given level."""
        self.calls.append({"message": message, "level": level})
        return {
            "reply": f"stub reply for level {level}",
            "level": level,
            "provider": "local",
            "model": "stub-model",
            "confidence": 0.9,
        }

    async def think_enhanced(self, message: str, level: int = 1) -> "ScriptedEnhancedResponse":
        """Return an enhanced response for the given level."""
        self.calls.append({"message": message, "level": level, "enhanced": True})
        return ScriptedEnhancedResponse(f"stub reply for level {level}")

    def enhance_response(self, message: str, raw_reply: str) -> "ScriptedEnhancedResponse":
        """Scripted enhancer - returns raw reply unchanged wrapped in EnhancedResponse-like object."""
        return ScriptedEnhancedResponse(raw_reply)

    async def stream(self, message: str, level: int = 1):
        """Yield a canned stream with token events."""
        self.calls.append({"message": message, "level": level, "stream": True})
        for token in ("stub ", "stream"):
            yield {"type": "token", "content": token}
        yield {
            "type": "done",
            "level": level,
            "streamed_level": level,
            "reason": "scripted",
            "provider": "stub",
            "chars": 10,
            "degraded": False,
        }


class ScriptedEnhancedResponse:
    """Minimal EnhancedResponse mock for tests."""

    def __init__(self, reply: str):
        self.reply = reply
        self.mood = "neutral"
        self.thinking_steps = []
        self.confidence = None
        self.continuation = None
        self.suggestions = []
        self.critique = None
        self.variation_meta = {}
        self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "mood": self.mood,
            "thinking_steps": [],
            "confidence": None,
            "continuation": None,
            "suggestions": [],
            "critique": None,
            "variation_meta": {},
            "metadata": {},
        }


@pytest.fixture()
def client() -> TestClient:
    """Build a test client whose Thalamus uses the stub Cerebrum."""
    app = create_app()
    app.state.thalamus.cerebrum = StubCerebrum()
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient) -> None:
    """The liveness probe reports ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_page_is_served(client: TestClient) -> None:
    """The chat UI is served at the root."""
    response = client.get("/")
    assert response.status_code == 200
    assert "ZUZZ" in response.text


def test_killswitch_script_is_served(client: TestClient) -> None:
    """The Stop control script is a static asset like the others."""
    assert client.get("/static/js/killswitch.js").status_code == 200


def test_stop_button_is_in_the_ui(client: TestClient) -> None:
    """The UI carries a Stop control, because a hotkey is easy to forget."""
    html = client.get("/").text
    assert 'id="stop-btn"' in html
    assert 'id="stop-banner"' in html
    assert "killswitch.js" in html


def test_static_css_is_served(client: TestClient) -> None:
    """Static assets are mounted."""
    assert client.get("/static/css/main.css").status_code == 200


def test_chat_endpoint_returns_stub_reply(client: TestClient) -> None:
    """A chat message comes back with routing metadata."""
    response = client.post("/api/chat", json={"message": "hello there"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("stub reply")
    assert "reason" in body and "degraded" in body


def test_chat_rejects_empty_message(client: TestClient) -> None:
    """Whitespace-only input is rejected with 400."""
    response = client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 400


def test_forced_level_is_honoured(client: TestClient) -> None:
    """A client-supplied level bypasses the analyzer."""
    response = client.post("/api/chat", json={"message": "hi", "level": 2})
    assert response.status_code == 200
    data = response.json()
    # The forced level flow returns the enhanced response with "reason": "forced level"
    assert data.get("reason") == "forced level"
    # The level is used internally and reflected in the reply
    assert "level 2" in data.get("reply", "") or "level 2" in data.get("response", "")


def test_classify_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Classification is available without calling a model.

    Connectivity is stubbed so the assertion tests the classifier rather than
    the CI machine's network. Without internet the analyzer caps every level at
    2 by design, which would mask the complex-reasoning match entirely.
    """
    monkeypatch.setattr(routes_chat, "internet_available", _always_online)
    response = client.post("/api/classify", json={"message": "debug this stack trace"})
    assert response.status_code == 200
    assert response.json()["level"] >= 3


def test_classify_endpoint_caps_level_when_offline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same debug message falls back to a local level with no internet."""
    monkeypatch.setattr(routes_chat, "internet_available", _always_offline)
    response = client.post("/api/classify", json={"message": "debug this stack trace"})
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == 2
    assert body["offline_fallback"] is True


def test_brain_status_endpoint(client: TestClient) -> None:
    """The dashboard status endpoint aggregates every component."""
    body = client.get("/api/brain/status").json()
    assert "heartbeat" in body and "thalamus" in body
    assert "recent_activity" in body


def test_activity_log_records_the_request(client: TestClient) -> None:
    """Every routed message is written to the immutable activity log."""
    client.post("/api/chat", json={"message": "log me please"})
    entries = client.get("/api/brain/activity?count=50").json()["entries"]
    actions = {entry["action"] for entry in entries}
    assert "route" in actions


def test_settings_route_rejects_immutable_keys(client: TestClient) -> None:
    """Trust level cannot be changed through the settings API."""
    response = client.post("/api/settings", json={"trust_level": 5})
    assert response.status_code == 400
    assert "not editable" in response.json()["detail"]


def test_settings_route_accepts_safe_keys(client: TestClient) -> None:
    """Non-safety settings can be updated."""
    response = client.post("/api/settings", json={"streaming": False})
    assert response.status_code == 200
    assert response.json()["applied"] == {"streaming": False}


def test_plugins_endpoint_lists_registry(client: TestClient) -> None:
    """The plugin registry is readable."""
    body = client.get("/api/plugins").json()
    assert "installed" in body and "files_on_disk" in body


def test_websocket_streams_tokens_then_done(client: TestClient) -> None:
    """A WebSocket chat streams tokens followed by a done event."""
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"message": "stream this", "level": None})
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in ("done", "error"):
                break
    assert [e["type"] for e in events[:-1]] == ["token", "token"]
    assert events[-1]["type"] == "done"


def test_websocket_reports_empty_message(client: TestClient) -> None:
    """An empty WebSocket message produces an error event, not a crash."""
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"message": ""})
        assert websocket.receive_json()["type"] == "error"
