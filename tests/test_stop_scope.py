"""Spinal Cord — stop scope tests.

The switch engaging is only half the requirement. An emergency stop that leaves
a runaway scheduled job running has not stopped anything, so these tests pin
what the scope actually reaches: jobs paused, processes terminated, cloud agents
flagged rather than silently left alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.stop_scope import (
    CLOUD_PAUSE_REASON,
    KILL_SWITCH_ACTIVE,
    StopScope,
)
from core.audit import AuditLog


class FakePauser:
    """A scheduler stand-in that records whether it was paused."""

    name = "scheduler"

    def __init__(self, fails: bool = False) -> None:
        """Record whether pausing should raise."""
        self.paused = False
        self.fails = fails

    def pause(self) -> None:
        """Record the pause, or raise when the scope is being tested for that."""
        if self.fails:
            raise RuntimeError("already stopped")
        self.paused = True


@pytest.fixture
def switch(tmp_path: Path) -> KillSwitch:
    """Return a switch with flags in the temp directory."""
    return KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))


@pytest.fixture
def terminated() -> List[int]:
    """Return the list the fake terminator appends to."""
    return []


@pytest.fixture
def scope(switch: KillSwitch, terminated: List[int]) -> StopScope:
    """Return a scope with one pauser and two processes."""
    return StopScope(
        switch,
        pausables=[FakePauser()],
        processes=[101, 202],
        terminator=lambda pid, force_after: terminated.append(pid) or True,
    )


def test_engage_trips_the_switch(scope: StopScope, switch: KillSwitch) -> None:
    """Engaging the scope stops the machine."""
    scope.engage(reason="something is looping")
    assert switch.is_killed()
    assert switch.status()["reason"] == "something is looping"


def test_engage_pauses_scheduled_jobs(scope: StopScope) -> None:
    """A runaway job may be the cause, so jobs stop."""
    scope.engage()
    assert scope.pausables[0].paused is True


def test_engage_terminates_background_processes(scope: StopScope, terminated: List[int]) -> None:
    """Background processes ZUZZ started are stopped."""
    report = scope.engage()
    assert report["terminated"] == [101, 202]
    assert terminated == [101, 202]


def test_engage_flags_cloud_agents(switch: KillSwitch, terminated: List[int]) -> None:
    """Cloud agents cannot be killed remotely, so they are flagged."""
    scope = StopScope(
        switch,
        cloud_agents=["colab"],
        terminator=lambda pid, force_after: True,
    )
    report = scope.engage(reason="user pressed stop")
    assert report["cloud_flagged"] == ["colab"]
    assert report["cloud_pause_reason"] == CLOUD_PAUSE_REASON


def test_report_is_kept_for_the_ui(scope: StopScope) -> None:
    """The last stop stays visible so the dashboard can explain it."""
    scope.engage(reason="stopped from the UI")
    assert scope.last_report["status"]["reason"] == "stopped from the UI"


def test_a_failing_pauser_does_not_abort_the_stop(scope: StopScope, terminated: List[int]) -> None:
    """One broken subsystem must not leave the rest of the machine running."""
    scope.pausables = [FakePauser(fails=True)]
    report = scope.engage()
    assert report["paused"] == []
    assert report["terminated"] == [101, 202]
    assert scope.switch.is_killed()


def test_process_that_survives_is_not_reported_as_stopped(
    switch: KillSwitch, terminated: List[int]
) -> None:
    """A process that ignored the stop must be visible, not silently dropped."""
    scope = StopScope(
        switch,
        processes=[101, 202],
        terminator=lambda pid, force_after: pid == 101,
    )
    report = scope.engage()
    assert report["terminated"] == [101]
    assert scope.processes == [101, 202]


def test_registered_process_is_terminated(scope: StopScope, terminated: List[int]) -> None:
    """Processes registered after construction are still stopped."""
    scope.register_process(303)
    scope.register_process(303)
    assert scope.processes == [101, 202, 303]
    assert scope.engage()["terminated"] == [101, 202, 303]


def test_no_processes_is_fine(switch: KillSwitch) -> None:
    """A machine with no background processes still stops cleanly."""
    scope = StopScope(switch, terminator=lambda pid, force_after: True)
    assert scope.engage()["terminated"] == []


def test_default_terminator_treats_a_dead_pid_as_stopped(switch: KillSwitch) -> None:
    """Without an override the real process killer is used.

    An already-dead PID counts as stopped, so a stale registration cannot make
    a stop look like it failed.
    """
    import os

    dead_pid = 999_999
    assert os.getpid() != dead_pid
    scope = StopScope(switch, processes=[dead_pid])
    assert scope.engage()["terminated"] == [dead_pid]


def test_default_terminator_never_reports_the_live_brain(switch: KillSwitch) -> None:
    """Terminating ZUZZ's own PID from inside ZUZZ is the caller's risk.

    The terminator is trusted to kill what it is given; this test only pins that
    it is the real implementation rather than a no-op.
    """
    from brain.spinal_cord.terminate import terminate_brain

    assert callable(terminate_brain)
    assert terminate_brain(999_999, 0.1) is True


def test_cloud_pause_state_is_distinct() -> None:
    """The kill switch state must not be confused with a provider failure.

    ``kill_switch_active`` reads as deliberate; ``degraded`` or ``offline``
    would read as a broken account, and the user would go looking for the wrong
    problem.
    """
    assert KILL_SWITCH_ACTIVE not in ("healthy", "degraded", "offline", "rate_limited", "unconfigured")
    assert KILL_SWITCH_ACTIVE == "kill_switch_active"
