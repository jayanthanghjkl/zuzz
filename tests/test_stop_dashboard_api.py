"""Spinal Cord — dashboard and provider integration tests.

What the stop looks like from outside the kill switch's own routes: the provider
list, the watchdog controls and the brain status the dashboard polls. Each of
these must tell the truth about a stopped machine — showing a healthy provider
while the gate denies everything would be the worst possible failure mode.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(kill_switch_app) -> TestClient:
    """Build a client over the app with an isolated kill switch."""
    with TestClient(kill_switch_app) as test_client:
        yield test_client


def stop(client: TestClient) -> None:
    """Engage the switch through the API."""
    client.post("/api/kill-switch/stop", json={"reason": "testing", "source": "ui"})


def do_reset(client: TestClient) -> None:
    """Reset the switch through the API."""
    client.post("/api/kill-switch/reset", json={"typed_word": "RESET", "confirmed": True})


# -- providers --------------------------------------------------------------


def test_providers_are_flagged_while_stopped(client: TestClient) -> None:
    """Cloud agents show as paused, not as broken."""
    stop(client)
    body = client.get("/api/providers").json()
    assert body["kill_switch_active"] is True
    assert all(p["detail"] == "kill switch active" for p in body["providers"])


def test_providers_use_a_distinct_state_while_stopped(client: TestClient) -> None:
    """A paused provider must not read as a failed or unconfigured one."""
    stop(client)
    for provider in client.get("/api/providers").json()["providers"]:
        assert provider["state"] == "kill_switch_active"


def test_providers_are_normal_after_reset(client: TestClient) -> None:
    """Once reset, provider health is reported normally again."""
    stop(client)
    do_reset(client)
    assert client.get("/api/providers").json()["kill_switch_active"] is False


def test_provider_state_is_normal_while_running(client: TestClient) -> None:
    """A running machine reports real health, not the pause state."""
    body = client.get("/api/providers").json()
    assert body["kill_switch_active"] is False
    assert all(p["state"] != "kill_switch_active" for p in body["providers"])


# -- watchdog ---------------------------------------------------------------


def test_watchdog_poll_is_readable(client: TestClient) -> None:
    """The dashboard can see what the watchdog considers normal."""
    body = client.get("/api/kill-switch/watchdog").json()
    assert "cpu_percent" in body and "trips" in body


def test_watchdog_thresholds_can_be_set(client: TestClient) -> None:
    """Thresholds are tunable, so a slow machine can be given headroom."""
    response = client.post(
        "/api/kill-switch/watchdog", json={"cpu_percent": 80, "deletion_limit": 5}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cpu_percent"] == 80
    assert body["deletion_limit"] == 5


def test_watchdog_streak_limit_can_be_set(client: TestClient) -> None:
    """The streak threshold is tunable alongside the CPU one."""
    response = client.post("/api/kill-switch/watchdog", json={"cpu_streak_limit": 2})
    assert response.status_code == 200
    assert response.json()["cpu_streak_limit"] == 2


def test_negative_thresholds_are_refused(client: TestClient) -> None:
    """A negative threshold would trip on every poll."""
    assert client.post("/api/kill-switch/watchdog", json={"cpu_percent": -1}).status_code == 400


def test_unknown_thresholds_are_ignored(client: TestClient) -> None:
    """Only the documented keys are settable."""
    response = client.post("/api/kill-switch/watchdog", json={"nonsense": 5})
    assert response.status_code == 200
    assert "nonsense" not in response.json()


# -- brain status -----------------------------------------------------------


def test_brain_status_includes_the_switch(client: TestClient) -> None:
    """The dashboard shows the stop state alongside everything else."""
    body = client.get("/api/brain/status").json()
    assert body["kill_switch"]["engaged"] is False
    assert body["scheduler"]["paused"] is False


def test_brain_status_reflects_a_stop(client: TestClient) -> None:
    """After a stop the same endpoint tells the dashboard ZUZZ is stopped."""
    stop(client)
    body = client.get("/api/brain/status").json()
    assert body["kill_switch"]["engaged"] is True
    assert body["scheduler"]["paused"] is True


def test_brain_status_lists_the_registered_jobs(client: TestClient) -> None:
    """Jobs stay visible while paused, so the user can see what was stopped.

    ``trash_purge`` is Step 4's daily auto-purge of the trash; it is
    listed here because a job the user cannot see is a job they cannot
    account for. ``resource_cycle`` is Step 6's Hypothalamus tick, which reads
    the sensors and evicts a model before VRAM runs out — the same reason.
    """
    jobs = client.get("/api/brain/status").json()["scheduler"]["jobs"]
    assert {job["name"] for job in jobs} == {
        "heartbeat",
        "model_reaper",
        "resource_cycle",
        "trash_purge",
    }
