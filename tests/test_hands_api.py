"""Face API — the desktop routes.

The point of these tests is the *flow*, not the endpoints: a request must be
scored, decided, and either executed or turned into a confirmation prompt before
anything reaches the desktop. The three states every endpoint can be in —
``ok``, ``confirmation_required`` and refused — are each checked on an endpoint
that stands for a whole family of operations.

Every test replaces ``app.state.hands`` with a Hands layer wired to a recording
actuator, so the suite cannot reach the developer's mouse and keyboard even if a
guard were removed. The recurring assertion is that a refused request left
``actuator.calls`` empty.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.stop_state import SOURCE_UI

from body.hands.hands import Hands
from body.hands.recorder import RecordingActuator
from face.api.routes_hands import router
from face.api.server import app

FRONTEND = Path(__file__).resolve().parent.parent / "face" / "frontend"


class StubClipboard:
    """A clipboard that never touches the system one."""

    def __init__(self, content: str = "") -> None:
        self.content = content

    def paste(self) -> str:
        return self.content

    def write(self, text: str) -> None:
        self.content = text


class StubLauncher:
    """Records launches instead of performing them."""

    def __init__(self) -> None:
        self.launched: list = []

    def __call__(self, command: str):
        self.launched.append(command)
        return type("P", (), {"pid": 1})()


@pytest.fixture
def actuator() -> RecordingActuator:
    """A recording actuator. Nothing here reaches the hardware."""
    return RecordingActuator()


@pytest.fixture
def client(actuator, tmp_path) -> TestClient:
    """A test client whose Hands layer cannot touch the desktop or the real stop.

    The kill switch is replaced with one confined to ``tmp_path`` rather than
    using the process-wide singleton. That matters more here than in the Hands
    tests: these tests *engage* the stop to prove it outranks everything, and the
    singleton writes its flag to the developer's real storage. A test that leaves
    a stop flag behind disables the running assistant, so the isolated switch is
    not tidiness — it is the difference between a passing suite and a machine
    that refuses to start next morning.
    """
    from brain.spinal_cord.kill_switch import KillSwitch
    from core.audit import AuditLog

    switch = KillSwitch(
        paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl")
    )
    app.state.kill_switch = switch
    hands = Hands(
        actuator=actuator,
        kill_switch=switch,
        logger=app.state.logger,
        clipboard_backend=StubClipboard(),
        launcher=StubLauncher(),
        foreground=lambda: {
            "fullscreen": False,
            "title": "",
            "window": {"title": "test"},
        },
        elevated=False,
    )
    app.state.hands = hands
    # Hands reads the display size while building its Screens. That probe is
    # setup, not an action, and clearing it keeps "nothing was recorded" a
    # statement about the request rather than about construction.
    actuator.calls.clear()
    app.state.trust = TrustManager(tmp_path / "trust.json")
    app.state.trust.save(5)
    return TestClient(app)


def operations(client: TestClient) -> dict:
    """Return a representative request for each operation family."""
    return {
        "click": ("/api/hands/click", {"x": 100, "y": 100}),
        "type": ("/api/hands/type", {"text": "hello"}),
        "hotkey": ("/api/hands/hotkey", {"keys": ["ctrl", "c"]}),
        "open_app": ("/api/hands/open-app", {"name": "chrome"}),
        "close_app": ("/api/hands/close-app", {"name": "notepad"}),
    }


class TestAtTrustLevelFive:
    """Level 5 is autonomy over routine work, and the flow still runs."""

    def test_a_click_executes(self, client, actuator) -> None:
        """An ``auto`` decision reaches the actuator and returns 200."""
        response = client.post("/api/hands/click", json={"x": 100, "y": 100})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        assert actuator.ops() == ["click"]

    def test_typing_executes(self, client, actuator) -> None:
        """Ordinary text is typed."""
        response = client.post("/api/hands/type", json={"text": "hello"})
        assert response.status_code == 200, response.text
        assert actuator.ops() == ["write"]

    def test_the_response_carries_the_decision(self, client) -> None:
        """The UI shows why an action was allowed, not just that it was."""
        body = client.post("/api/hands/click", json={"x": 100, "y": 100}).json()
        assert body["decision"]["trust_level"] == 5
        assert body["decision"]["allowed"] is True

    def test_a_dry_run_describes_without_acting(self, client, actuator) -> None:
        """A preview must not move the cursor."""
        response = client.post(
            "/api/hands/click", json={"x": 100, "y": 100, "dry_run": True}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "dry_run"
        assert response.json()["plan"]["description"]
        assert actuator.calls == []


class TestRefusalsBecomeStatuses:
    """The exception hierarchy becomes an HTTP status."""

    def test_an_off_screen_point_is_403(self, client, actuator) -> None:
        """A point on no monitor is a refusal, and nothing is recorded."""
        response = client.post("/api/hands/click", json={"x": 99999, "y": 99999})
        assert response.status_code == 403
        assert actuator.calls == []

    def test_a_blocked_hotkey_is_403(self, client, actuator) -> None:
        """Blocked combinations never reach the gate, so there is no prompt."""
        response = client.post(
            "/api/hands/hotkey", json={"keys": ["ctrl", "alt", "delete"]}
        )
        assert response.status_code == 403
        assert actuator.calls == []

    def test_zuzzs_own_stop_is_403(self, client, actuator) -> None:
        """Refused for a different reason, but the same status."""
        response = client.post(
            "/api/hands/hotkey", json={"keys": ["ctrl", "shift", "q"]}
        )
        assert response.status_code == 403
        assert actuator.calls == []

    def test_dangerous_text_is_403(self, client, actuator) -> None:
        """Refused rather than confirmed, so there is no prompt to show."""
        response = client.post("/api/hands/type", json={"text": "rm -rf /"})
        assert response.status_code == 403
        assert actuator.calls == []

    def test_a_never_launch_app_is_403(self, client) -> None:
        """A whitelist entry that opens a shell is refused outright."""
        response = client.post(
            "/api/hands/open-app", json={"name": "powershell.exe -enc QQ=="}
        )
        assert response.status_code == 403

    def test_a_bad_button_is_400(self, client, actuator) -> None:
        """A typo is the caller's problem, not a safety event."""
        response = client.post(
            "/api/hands/click", json={"x": 10, "y": 10, "button": "foot"}
        )
        assert response.status_code == 400
        assert actuator.calls == []

    def test_the_detail_explains_the_refusal(self, client) -> None:
        """A refusal the user cannot understand is a refusal they will bypass."""
        detail = client.post("/api/hands/type", json={"text": "rm -rf /"}).json()["detail"]
        assert "refusing to type" in detail


class TestTheStopOutranksEverything:
    """While the stop is engaged, nothing is executed."""

    def test_an_engaged_switch_blocks_every_endpoint(self, client, actuator) -> None:
        """Every family of operation returns 409 and records nothing."""
        from brain.spinal_cord.reset_policy import RESET_WORD

        switch = client.app.state.kill_switch
        switch.trigger(reason="test", source=SOURCE_UI)
        try:
            for path, payload in operations(client).values():
                response = client.post(path, json=payload)
                assert response.status_code == 409, (path, response.text)
            assert actuator.calls == []
        finally:
            switch.reset(typed_word=RESET_WORD, confirmed=True)


class TestTheDecisionFreeEndpoints:
    """The three documented exceptions."""

    def test_layout_asks_nothing(self, client) -> None:
        """Reading the display layout is configuration, not an action."""
        response = client.get("/api/hands/layout")
        assert response.status_code == 200
        assert "monitors" in response.json()["layout"]

    def test_policy_asks_nothing(self, client) -> None:
        """Showing the user what ZUZZ will refuse is the opposite of an action."""
        policy = client.get("/api/hands/policy").json()["policy"]
        assert "chrome" in policy["apps"]["apps"]
        assert policy["hotkeys"]["always_blocked"]

    def test_release_keys_asks_nothing(self, client, actuator) -> None:
        """Cleanup for an operation that has already failed."""
        response = client.post("/api/hands/release-keys", json={})
        assert response.status_code == 200
        assert response.json()["released"] == []


class TestClipboardRoutes:
    """The clipboard read escalates before the gate sees it."""

    def test_ordinary_clipboard_reads_at_level_five(self, client) -> None:
        """Most clipboard content is not a secret."""
        response = client.get("/api/hands/clipboard")
        assert response.status_code == 200
        assert response.json()["credential_finding"] is None

    def test_a_credential_read_asks_even_at_level_five(self, client) -> None:
        """``access_credentials`` asks at every level, including five."""
        client.app.state.hands.clipboard._backend = StubClipboard(
            "sk-abcdefghijklmnopqrstuvwx"
        )
        response = client.get("/api/hands/clipboard")
        assert response.status_code == 403
        assert "access credentials" in response.json()["detail"].lower()

    def test_a_credential_is_never_returned(self, client) -> None:
        """The escalation happens before the content is handed back."""
        client.app.state.hands.clipboard._backend = StubClipboard(
            "sk-abcdefghijklmnopqrstuvwx"
        )
        assert "sk-abcdefghijklmnop" not in client.get("/api/hands/clipboard").text


class TestResolutionPrecedesTheGate:
    """The gate decides about the executable, not the alias."""

    def test_an_alias_launches_its_executable(self, client) -> None:
        """``chrome`` launches ``chrome.exe`` and the decision names the latter."""
        response = client.post("/api/hands/open-app", json={"name": "chrome"})
        assert response.status_code == 200, response.text
        assert client.app.state.hands.apps.launcher.launched == ["chrome.exe"]


class TestTheFrontendAgreesWithTheRoutes:
    """The Desktop page must not call an endpoint that does not exist.

    A typo in a fetch path fails silently in the browser — the user sees a
    button that does nothing — so the URLs the JavaScript calls are checked
    against the routes that exist.
    """

    def test_every_hands_url_the_page_calls_exists(self) -> None:
        """No dead buttons."""
        source = (FRONTEND / "static" / "js" / "hands.js").read_text(encoding="utf-8")
        called = set(re.findall(r"[\"'](/api/hands/[a-z-]+)[\"']", source))
        assert called, "the page should call at least one endpoint"
        registered = {route.path for route in router.routes}
        assert sorted(called - registered) == []

    def test_the_page_exists(self) -> None:
        """The Desktop tab has a page behind it."""
        assert (FRONTEND / "pages" / "hands.html").is_file()

    def test_every_page_links_to_the_others(self) -> None:
        """No tab is a dead end.

        ``files.html`` and ``hands.html`` are those pages, so neither is asked to
        link to itself; ``index.html`` must offer both.
        """
        index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        assert "/pages/hands.html" in index
        assert "/pages/files.html" in index
        for page, other in (("pages/files.html", "hands"), ("pages/hands.html", "files")):
            text = (FRONTEND / page).read_text(encoding="utf-8")
            assert f"/pages/{other}.html" in text, page
            assert 'href="/"' in text, page