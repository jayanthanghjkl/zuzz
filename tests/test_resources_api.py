"""Hypothalamus API tests.

The routes are where a permission check becomes something an agent could skip,
so these tests attack the HTTP surface directly rather than the manager: the
manager's own suite proves the cap holds, and this one proves that reaching the
manager at all required a decision.

The recurring assertion is that a refused call left *no trace* — no model loaded,
no budget spent. A 409 that still loaded the model would pass a status-code check
while leaving the user's VRAM committed to something they never approved.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from brain.hypothalamus.model_manager import ModelManager
from brain.hypothalamus.vram_budget import VRAMBudget

SMALL_MODELS = {
    "tiny-model": {"vram_mb": 600, "ram_mb": 200, "priority": "on_demand", "role": "vision"},
    # 3000MB fits alone in 3108MB usable, and never fits alongside anything.
    "large-model": {"vram_mb": 3000, "ram_mb": 700, "priority": "on_demand", "role": "deep_reasoning"},
    # Larger than the usable space, so it never fits at any occupancy.
    "oversized-model": {"vram_mb": 4000, "ram_mb": 900, "priority": "on_demand", "role": "deep_reasoning"},
    "core-model": {"vram_mb": 700, "ram_mb": 300, "priority": "always_loaded", "role": "fast_responses"},
}


class FakeTransport:
    """A transport that records instead of touching VRAM."""

    def __init__(self, fail: bool = False) -> None:
        """Optionally refuse every load, to exercise the 503 mapping."""
        self.calls: List[tuple] = []
        self.resident: set = set()
        self.fail = fail

    async def load(self, model: str, timeout: float) -> None:
        """Record a load."""
        from brain.hypothalamus.exceptions import ModelLoadError

        self.calls.append(("load", model))
        if self.fail:
            raise ModelLoadError("simulated backend failure")
        self.resident.add(model)

    async def unload(self, model: str, timeout: float) -> None:
        """Record an unload."""
        self.calls.append(("unload", model))
        self.resident.discard(model)


@pytest.fixture
def resources(kill_switch_app):
    """Return a client whose model manager cannot reach real hardware.

    Args:
        kill_switch_app: The isolated app from ``conftest``.

    Returns:
        ``(client, transport)``.
    """
    app = kill_switch_app
    transport = FakeTransport()
    app.state.hypothalamus.models = ModelManager(
        budget=VRAMBudget(total_mb=4608, headroom_mb=1500),
        models=SMALL_MODELS,
        transport=transport,
        gate=app.state.permission_gate,
        trust_level=1,
        audit=app.state.audit,
        logger=app.state.logger,
    )
    return TestClient(app), transport


def test_reading_resources_needs_no_permission(resources) -> None:
    """The gauges are ungated, like the trust level display."""
    client, _ = resources
    for path in (
        "/api/resources/ram",
        "/api/resources/vram",
        "/api/resources/cpu",
        "/api/resources/gpu",
        "/api/resources/disk",
        "/api/resources/snapshot",
        "/api/power/mode",
        "/api/thermal/status",
        "/api/thermal/history",
        "/api/models/loaded",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_vram_route_labels_measured_or_estimated(resources) -> None:
    """The UI is told which kind of number it is looking at."""
    client, _ = resources
    body = client.get("/api/resources/vram").json()
    assert body["reporting"] in ("measured", "estimated")
    assert body["budget"]["headroom_mb"] == 1500
    assert body["budget"]["usable_mb"] == 3108


def test_can_fit_reports_a_reason(resources) -> None:
    """A refusal carries a reason the Thalamus can route on."""
    client, _ = resources
    small = client.get("/api/models/can-fit", params={"model": "tiny-model"}).json()
    assert small["allowed"] is True
    large = client.get("/api/models/can-fit", params={"model": "oversized-model"}).json()
    assert large["allowed"] is False
    assert "headroom" in large["reason"]
    unknown = client.get("/api/models/can-fit", params={"model": "nope"}).json()
    assert unknown["allowed"] is False
    assert "no profile" in unknown["reason"]


def test_load_at_level_one_parks_a_confirmation_and_loads_nothing(resources) -> None:
    """The 202 path executes nothing.

    ``manage_models`` asks at level 1. The route must park a confirmation and
    leave the model unloaded — a 202 that had already loaded the model would be a
    permission granted by returning a status code.
    """
    client, transport = resources
    response = client.post("/api/models/load", json={"model": "tiny-model"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmation_required"
    assert body["confirmation_id"]
    assert transport.calls == []
    assert client.get("/api/models/loaded").json()["loaded"] == []


def test_a_confirmed_load_at_level_one_proceeds(resources) -> None:
    """Answering the parked confirmation loads the model exactly once."""
    client, transport = resources
    parked = client.post("/api/models/load", json={"model": "tiny-model"}).json()
    response = client.post(
        "/api/models/load",
        json={"model": "tiny-model", "confirm_id": parked["confirmation_id"]},
    )
    assert response.status_code == 200
    assert response.json()["result"]["loaded"] is True
    assert transport.calls == [("load", "tiny-model")]
    loaded = client.get("/api/models/loaded").json()["loaded"]
    assert [m["model"] for m in loaded] == ["tiny-model"]


def test_a_confirmation_cannot_be_replayed(resources) -> None:
    """A confirmation is spent once. Replaying it does not load a second model.

    Confirmations live in memory precisely so they cannot outlive their prompt,
    and a spent id must not be redeemable a second time — that would turn one
    dialog into unlimited authority.
    """
    client, transport = resources
    parked = client.post("/api/models/load", json={"model": "tiny-model"}).json()
    first = client.post(
        "/api/models/load",
        json={"model": "tiny-model", "confirm_id": parked["confirmation_id"]},
    )
    assert first.status_code == 200
    replay = client.post(
        "/api/models/load",
        json={"model": "tiny-model", "confirm_id": parked["confirmation_id"]},
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["error"] == "unknown_confirmation"
    assert transport.calls == [("load", "tiny-model")]


def test_a_confirmation_cannot_be_spent_on_a_different_model(resources) -> None:
    """A confirmation for one model does not authorise loading another.

    Without the match check, a dialog the user answered about ``tiny-model``
    becomes general permission to load whatever the caller names next.
    """
    client, transport = resources
    parked = client.post("/api/models/load", json={"model": "tiny-model"}).json()
    swapped = client.post(
        "/api/models/load",
        json={"model": "core-model", "confirm_id": parked["confirmation_id"]},
    )
    assert swapped.status_code == 400
    assert swapped.json()["detail"]["error"] == "confirmation_mismatch"
    assert transport.calls == []


def test_an_unknown_confirmation_is_refused(resources) -> None:
    """A fabricated confirmation id loads nothing."""
    client, transport = resources
    response = client.post(
        "/api/models/load", json={"model": "tiny-model", "confirm_id": "conf-forged-1"}
    )
    assert response.status_code == 400
    assert transport.calls == []


def test_the_kill_switch_outranks_a_model_load(resources) -> None:
    """While ZUZZ is stopped, model management is denied whatever the trust level."""
    client, transport = resources
    app = client.app
    app.state.trust.save(5)
    app.state.kill_switch.trigger(source="ui", reason="api suite")
    try:
        response = client.post("/api/models/load", json={"model": "tiny-model"})
        assert response.status_code == 409
        assert "kill switch" in response.json()["detail"]["reason"].lower()
        assert transport.calls == []
    finally:
        app.state.kill_switch.reset(typed_word="RESET", confirmed=True)
        app.state.trust.save(1)


def test_a_full_budget_is_429_not_500(resources) -> None:
    """Budget exhaustion is a resource condition, not a broken server."""
    client, transport = resources
    app = client.app
    app.state.trust.save(5)
    app.state.hypothalamus.models.trust_level = 5
    try:
        first = client.post("/api/models/load", json={"model": "large-model"})
        assert first.status_code == 200, first.text
        assert first.json()["result"]["loaded"] is True
        # 3000MB used of 3108MB: one 600MB model no longer fits.
        second = client.post("/api/models/load", json={"model": "tiny-model"})
        assert second.status_code == 429
        assert second.json()["detail"]["error"] == "vram_budget_exceeded"
        assert ("load", "tiny-model") not in transport.calls
    finally:
        app.state.trust.save(1)


def test_an_unknown_model_is_404(resources) -> None:
    """An unprofiled model is not found, and is never sent to the backend."""
    client, transport = resources
    app = client.app
    app.state.trust.save(5)
    app.state.hypothalamus.models.trust_level = 5
    try:
        response = client.post("/api/models/load", json={"model": "mystery"})
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "model_not_found"
        assert transport.calls == []
    finally:
        app.state.trust.save(1)


def test_a_backend_failure_is_503_and_frees_the_reservation(resources) -> None:
    """A failed load is a backend problem, and leaks no budget."""
    client, _ = resources
    app = client.app
    transport = FakeTransport(fail=True)
    app.state.hypothalamus.models.transport = transport
    app.state.trust.save(5)
    app.state.hypothalamus.models.trust_level = 5
    try:
        response = client.post("/api/models/load", json={"model": "tiny-model"})
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "model_backend_unavailable"
        assert app.state.hypothalamus.models.budget.allocated_mb == 0
    finally:
        app.state.trust.save(1)


def test_unloading_a_core_model_at_level_one_is_refused(resources) -> None:
    """A confirmation is offered, because the user can approve this one.

    The core-model rule needs trust level 3, so at level 1 the honest answer is
    "ask" rather than "no": the user is present and this is their machine.
    """
    client, transport = resources
    app = client.app
    app.state.hypothalamus.models.trust_level = 5
    app.state.trust.save(5)
    loaded = client.post("/api/models/load", json={"model": "core-model"})
    assert loaded.status_code == 200
    assert loaded.json()["result"]["loaded"] is True
    try:
        app.state.trust.save(2)
        response = client.post("/api/models/unload", json={"model": "core-model"})
        assert response.status_code == 200
        assert response.json()["status"] == "confirmation_required"
        assert ("unload", "core-model") not in transport.calls
    finally:
        app.state.trust.save(1)


def test_hot_swap_routes_through_the_same_gate(resources) -> None:
    """A swap needs a confirmation at level 1, exactly as a load does."""
    client, transport = resources
    response = client.post(
        "/api/models/hot-swap", json={"old_model": "core-model", "new_model": "tiny-model"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert transport.calls == []


def test_power_mode_can_be_set_and_an_unknown_one_is_400(resources) -> None:
    """The three modes work; anything else is refused with an explanation."""
    client, _ = resources
    assert client.post("/api/power/set-mode?mode=idle").json()["mode"] == "idle"
    assert client.post("/api/power/set-mode?mode=active").json()["mode"] == "active"
    bad = client.post("/api/power/set-mode?mode=ludicrous")
    assert bad.status_code == 400
    assert bad.json()["detail"]["error"] == "unknown_mode"


def test_thermal_status_reports_which_sensors_work(resources) -> None:
    """The thermal route reports sensor availability, not just a level.

    A missing CPU sensor on Windows is normal, and the UI has to be able to say
    so rather than showing a confident 0°C for hardware that was never read.
    """
    client, _ = resources
    body = client.get("/api/thermal/status").json()
    assert body["status"]["level"] in ("normal", "warning", "throttle", "critical", "unmonitored")
    assert set(body["status"]["sensors"]) == {"gpu", "cpu"}
    assert body["thresholds"]["gpu"]["throttle"] == 85
    assert body["thresholds"]["cpu"]["critical"] == 95


def test_thermal_history_is_bounded(resources) -> None:
    """The chart endpoint returns at most what is retained."""
    client, _ = resources
    body = client.get("/api/thermal/history?count=5000").json()
    assert body["retention_seconds"] == 3600
    assert body["count"] <= 3600
