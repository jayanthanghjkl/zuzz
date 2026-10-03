"""Spinal Cord — HTTP surface tests.

The stop must work while everything else is broken, so its routes get their own
tests rather than being folded into the general integration suite.

The app comes from the shared ``kill_switch_app`` fixture, whose switch is
isolated from the developer's real storage. See :mod:`tests.conftest`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.audit import AuditLog


@pytest.fixture
def client(kill_switch_app) -> TestClient:
    """Build a client over the app with an isolated kill switch."""
    with TestClient(kill_switch_app) as test_client:
        yield test_client


def stop(client: TestClient, reason: str = "testing", source: str = "ui"):
    """Engage the switch through the API."""
    return client.post("/api/kill-switch/stop", json={"reason": reason, "source": source})


def do_reset(client: TestClient, word: str = "RESET", confirmed: bool = True):
    """Reset the switch through the API."""
    return client.post("/api/kill-switch/reset", json={"typed_word": word, "confirmed": confirmed})


def check(client: TestClient, category: str = "chat", target: str = "") -> dict:
    """Ask the gate for a dry-run decision."""
    response = client.post(
        "/api/safety/check", json={"category": category, "target": target, "dry_run": True}
    )
    return response.json()


# -- status -----------------------------------------------------------------


def test_status_reports_running_by_default(client: TestClient) -> None:
    """A fresh ZUZZ is not stopped."""
    body = client.get("/api/kill-switch").json()
    assert body["engaged"] is False
    assert body["scheduler_paused"] is False


def test_status_exposes_the_reset_word(client: TestClient) -> None:
    """The UI needs to tell the user what to type."""
    assert client.get("/api/kill-switch").json()["reset_word"] == "RESET"


# -- stopping ---------------------------------------------------------------


def test_stop_needs_no_confirmation(client: TestClient) -> None:
    """The whole point of a stop is that it works while something is wrong."""
    response = stop(client, reason="user pressed stop")
    assert response.status_code == 200
    assert response.json()["status"]["engaged"] is True


def test_stop_pauses_the_scheduler(client: TestClient) -> None:
    """Background jobs stop, because a runaway job may be the cause."""
    stop(client)
    assert client.get("/api/kill-switch").json()["scheduler_paused"] is True
    assert client.app.state.scheduler.paused is True


def test_stop_flags_cloud_agents(client: TestClient) -> None:
    """Cloud agents cannot be killed remotely, so they are flagged."""
    report = stop(client).json()
    assert "colab" in report["cloud_flagged"]
    assert report["cloud_pause_reason"] == "kill switch active"


def test_stop_records_the_reason(client: TestClient) -> None:
    """The cause survives in the status and the activity log."""
    stop(client, reason="agent deleting files", source="watchdog")
    body = client.get("/api/kill-switch").json()
    assert body["reason"] == "agent deleting files"
    assert body["source"] == "watchdog"


def test_stop_rejects_an_unknown_source(client: TestClient) -> None:
    """Only the four documented sources may stop the machine."""
    assert stop(client, source="telepathy").status_code == 400


def test_stop_is_audited(client: TestClient, tmp_path: Path) -> None:
    """Engaging the stop is a safety event."""
    stop(client, reason="user pressed stop")
    audit = AuditLog(tmp_path / "audit.jsonl")
    assert any("user pressed stop" in event["summary"] for event in audit.by_event("kill_switch"))


def test_stopping_twice_is_idempotent(client: TestClient) -> None:
    """A second press must not overwrite the first reason."""
    stop(client, reason="first")
    stop(client, reason="second")
    assert client.get("/api/kill-switch").json()["reason"] == "first"


def test_stop_is_logged_as_activity(client: TestClient) -> None:
    """The stop also appears in the ordinary activity log."""
    stop(client, reason="testing")
    entries = client.get("/api/brain/activity?count=50").json()["entries"]
    assert any(entry["action"] == "stop" for entry in entries)


# -- the stop blocks the gate ----------------------------------------------


def test_every_action_is_blocked_while_stopped(client: TestClient) -> None:
    """The gate refuses everything while the switch is engaged."""
    stop(client)
    body = check(client)
    assert body["decision"] == "blocked"
    assert body["allowed"] is False
    assert "kill switch" in body["reason"]


def test_actions_are_allowed_again_after_reset(client: TestClient) -> None:
    """The reset re-arms the machine."""
    stop(client)
    do_reset(client)
    assert check(client)["allowed"] is True


# -- resetting --------------------------------------------------------------


def test_reset_requires_confirmation(client: TestClient) -> None:
    """Nothing self-heals through the API."""
    stop(client)
    assert do_reset(client, confirmed=False).status_code == 409
    assert client.get("/api/kill-switch").json()["engaged"] is True


def test_reset_requires_the_typed_word(client: TestClient) -> None:
    """The word is required even with confirmation."""
    stop(client)
    response = do_reset(client, word="maybe")
    assert response.status_code == 409
    assert "RESET" in response.json()["detail"]


def test_reset_resumes_the_scheduler(client: TestClient) -> None:
    """The reset puts background work back."""
    stop(client)
    do_reset(client)
    assert client.get("/api/kill-switch").json()["scheduler_paused"] is False


def test_reset_reports_the_cooldown(client: TestClient) -> None:
    """After the first reset the UI must show the wait before the next one."""
    stop(client)
    do_reset(client)
    stop(client)
    assert client.get("/api/kill-switch").json()["cooldown_remaining"] > 0


def test_reset_is_logged_as_activity(client: TestClient) -> None:
    """Re-arming the machine is recorded."""
    stop(client)
    do_reset(client)
    entries = client.get("/api/brain/activity?count=50").json()["entries"]
    assert any(entry["action"] == "reset" for entry in entries)
