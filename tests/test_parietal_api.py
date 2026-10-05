"""Parietal Lobe — API tests.

The endpoints, not the region. What these check is the layer in between: that every
read is gated, that a platform refusal becomes a status a client can act on, and that
the one endpoint which is deliberately ungated is the one that should be.

Each test swaps ``app.state.parietal_lobe`` for a region built from doubles, because
these tests are about HTTP behaviour — a 503 for a refused read is the same 503 whether
the refusal came from a fake or from Win32, and using the real desktop would make the
suite pass or fail depending on which window happened to be focused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brain.parietal_lobe import (
    AppTracker,
    ContextDetector,
    MouseTracker,
    ParietalLobe,
    ScreenInfo,
    SystemAwarenessUnavailable,
    WindowTracker,
)

WINDOW = {
    "hwnd": 7,
    "title": "main.py - Visual Studio Code",
    "pid": 555,
    "process": "Code.exe",
    "rect": {"left": 0, "top": 0, "width": 1400, "height": 900},
    "visible": True,
}

LAYOUT = [{"left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True}]


class StubSwitch:
    """A kill switch whose state the test controls."""

    def __init__(self, killed: bool = False) -> None:
        self.killed = killed

    def is_killed(self) -> bool:
        """Return whether the stop is engaged."""
        return self.killed


def build(windows=(WINDOW,), killed: bool = False, refuse_windows: bool = False) -> ParietalLobe:
    """Build a region from doubles.

    Args:
        windows: The window list every call returns.
        killed: Whether the kill switch is engaged.
        refuse_windows: Make the window enumeration refuse, to provoke the 503.

    Returns:
        The region.
    """

    class Backend:
        """A capture backend returning a fixed layout."""

        def layout(self):
            """Return the fixed single-monitor layout."""
            return [dict(monitor) for monitor in LAYOUT]

    screens = ScreenInfo(backend=Backend(), desktop=lambda: dict(LAYOUT[0]), dpi=lambda: 96)

    def enumerate_windows():
        """Return the window list, or refuse."""
        if refuse_windows:
            raise SystemAwarenessUnavailable("EnumWindows failed", detail="windows")
        return [dict(window) for window in windows]

    return ParietalLobe(
        window_tracker=WindowTracker(
            windows=enumerate_windows,
            foreground=lambda: (windows or [{}])[0].get("hwnd", 0) if windows else 0,
            states=lambda record: {"minimized": False, "maximized": True},
        ),
        screen_info=screens,
        app_tracker=AppTracker(
            process=lambda: [(555, "Code.exe")],
            usage=lambda pid: {"cpu_percent": 3.0, "memory_mb": 250.0},
            windows=enumerate_windows,
        ),
        mouse_tracker=MouseTracker(
            position=lambda: (640, 480),
            visible=lambda: True,
            monitors=screens.monitors,
        ),
        context_detector=ContextDetector(idle=lambda: 4),
        kill_switch=StubSwitch(killed),
    )


@pytest.fixture
def client(kill_switch_app):
    """Return a client whose Parietal Lobe is a double.

    Args:
        kill_switch_app: The isolated app from ``conftest``.

    Returns:
        ``(client, app)`` so a test can swap the region or engage the stop.
    """
    app = kill_switch_app
    app.state.parietal_lobe = build()
    return TestClient(app), app


READS = (
    "/api/parietal/active-window",
    "/api/parietal/screen-info",
    "/api/parietal/running-apps",
    "/api/parietal/mouse-position",
    "/api/parietal/context",
    "/api/parietal/snapshot",
)


class TestEveryReadAnswers:
    def test_each_read_answers(self, client) -> None:
        """All six return a result with the decision that permitted it."""
        api, _ = client
        for path in READS:
            response = api.get(path)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            assert body["status"] == "ok", path
            assert body["decision"]["category"] == "system_awareness", path

    def test_active_window_reports_the_application(self, client) -> None:
        """The window endpoint names the app, not just the title."""
        api, _ = client
        result = api.get("/api/parietal/active-window").json()["result"]

        assert result["app"] == "code"
        assert result["title"] == WINDOW["title"]

    def test_screen_info_reports_the_layout(self, client) -> None:
        """The screen endpoint returns a resolution and a monitor count."""
        api, _ = client
        result = api.get("/api/parietal/screen-info").json()["result"]

        assert result["resolution"] == {"width": 1920, "height": 1080}
        assert result["monitor_count"] == 1

    def test_running_apps_defaults_to_excluding_windowless_processes(self, client) -> None:
        """The privacy default holds over HTTP, not just in the region."""
        api, _ = client
        result = api.get("/api/parietal/running-apps").json()["result"]

        assert result["count"] == 1
        assert result["hidden_count"] == 0

    def test_running_apps_accepts_the_opt_in(self, client) -> None:
        """``include_hidden`` reaches the region as a query parameter."""
        api, _ = client
        result = api.get("/api/parietal/running-apps?include_hidden=true").json()["result"]

        assert result["count"] == 1

    def test_context_reports_its_evidence(self, client) -> None:
        """An inference always arrives with the reason and the evidence."""
        api, _ = client
        result = api.get("/api/parietal/context").json()["result"]

        assert result["context"] == "coding"
        assert result["reason"]
        assert result["evidence"]["app"] == "code"

    def test_mouse_position_names_its_monitor(self, client) -> None:
        """The pointer comes back with the monitor it is on."""
        api, _ = client
        result = api.get("/api/parietal/mouse-position").json()["result"]

        assert result["x"] == 640
        assert result["monitor_index"] == 0
        assert result["on_desktop"] is True

    def test_snapshot_has_every_slot(self, client) -> None:
        """One call returns all six, keyed as the region names them."""
        api, _ = client
        result = api.get("/api/parietal/snapshot").json()["result"]

        assert set(result) == {
            "active_window",
            "screen_info",
            "running_apps",
            "mouse_position",
            "context",
            "workspaces",
        }


class TestTheGate:
    def test_the_status_endpoint_is_not_gated(self, client) -> None:
        """Configuration is readable without a decision — like the trust level."""
        api, _ = client
        response = api.get("/api/parietal/status")

        assert response.status_code == 200
        assert response.json()["result"]["category"] == "system_awareness"

    def test_a_stop_blocks_every_read(self, client) -> None:
        """The emergency stop is honoured by all six, with 409."""
        api, app = client
        app.state.kill_switch.engage(reason="test")

        for path in READS:
            response = api.get(path)
            assert response.status_code == 409, (path, response.status_code)

    def test_a_stop_is_reported_before_the_gate_runs(self, client) -> None:
        """A stop is a 409, not a 403 — the user asked to stop, not to refuse."""
        api, app = client
        app.state.parietal_lobe = build(killed=True)

        response = api.get("/api/parietal/active-window")
        assert response.status_code == 409
        assert "kill switch" in response.json()["detail"].lower()

    def test_a_refused_platform_is_a_503_not_a_500(self, client) -> None:
        """"The OS would not answer" is a different problem from "ZUZZ is broken"."""
        api, app = client
        app.state.parietal_lobe = build(refuse_windows=True)

        response = api.get("/api/parietal/active-window")
        assert response.status_code == 503
        assert "EnumWindows" in response.json()["detail"]

    def test_a_missing_region_is_a_503(self, client) -> None:
        """A server with no region says so rather than raising deep in a route."""
        api, app = client
        app.state.parietal_lobe = None

        response = api.get("/api/parietal/active-window")
        assert response.status_code == 503

    def test_the_snapshot_survives_one_failed_slot(self, client) -> None:
        """A refused window read does not fail the whole snapshot."""
        api, app = client
        app.state.parietal_lobe = build(refuse_windows=True)

        response = api.get("/api/parietal/snapshot")
        assert response.status_code == 200

        result = response.json()["result"]
        assert result["active_window"]["failed"] is True
        assert result["context"]["context"] == "unknown"
        # The rest of the snapshot is still real.
        assert result["screen_info"]["monitor_count"] == 1


class TestTheCatalogue:
    def test_the_page_calls_no_dead_parietal_url(self) -> None:
        """Every parietal URL the frontend calls is a real route.

        The "no dead buttons" property, applied to the new widget. A typo in a fetch
        path fails silently in the browser — the user sees a widget that never
        populates and concludes ZUZZ does not know where they are.
        """
        import re

        from face.api.routes_parietal import router

        source = (
            __import__("pathlib").Path("face/frontend/static/js/hands.js")
        ).read_text(encoding="utf-8")
        called = set(re.findall(r"[\"'](/api/parietal/[a-z-]+)[\"']", source))
        registered = {route.path for route in router.routes}

        assert called, "the widget should call at least one parietal endpoint"
        assert sorted(called - registered) == []
