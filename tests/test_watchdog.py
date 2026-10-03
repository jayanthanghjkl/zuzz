"""Spinal Cord — watchdog CPU-signal tests.

The watchdog is the automatic half of the stop: it catches a runaway the user
cannot click through. What matters here is that it needs a *streak* of saturation
rather than one poll, and that it can trip the switch but never clear it.

The mass-deletion signal is pinned separately in ``test_watchdog_deletions.py``;
``watchdog_fakes.py`` holds the helpers both files share.
"""

from __future__ import annotations

from pathlib import Path

from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.stop_scope import StopScope
from brain.spinal_cord.watchdog import CPU_STREAK_LIMIT, Watchdog
from core.audit import AuditLog
from tests.watchdog_fakes import FakeSwitch, poll_until_trip, watchdog_with


# -- CPU --------------------------------------------------------------------


def test_a_single_busy_poll_does_not_trip() -> None:
    """One saturated poll can be a legitimate model load."""
    watchdog = watchdog_with(99.0)
    for _ in range(CPU_STREAK_LIMIT - 1):
        assert watchdog.poll()["tripped"] is False
    assert watchdog.poll()["tripped"] is True


def test_the_streak_resets_when_cpu_drops() -> None:
    """Saturation must be consecutive; a quiet poll clears the streak."""
    watchdog = watchdog_with(99.0)
    watchdog.poll()
    watchdog.poll()
    watchdog.max_cpu_percent = lambda: 10.0  # type: ignore[method-assign]
    assert watchdog.poll()["tripped"] is False
    watchdog.max_cpu_percent = lambda: 99.0  # type: ignore[method-assign]
    for _ in range(CPU_STREAK_LIMIT - 1):
        assert watchdog.poll()["tripped"] is False


def test_idle_machine_never_trips() -> None:
    """The watchdog must not stop a working brain."""
    watchdog = watchdog_with(5.0)
    for _ in range(CPU_STREAK_LIMIT * 3):
        assert watchdog.poll()["tripped"] is False
    assert watchdog.trips == 0


def test_trip_names_the_cpu_reading() -> None:
    """The audit trail says what was measured."""
    report = poll_until_trip(watchdog_with(99.0))
    assert "cpu" in report["reason"]
    assert "99" in report["reason"]


def test_threshold_is_configurable() -> None:
    """A stricter streak trips sooner."""
    watchdog = watchdog_with(99.0, cpu_streak_limit=2)
    watchdog.poll()
    assert watchdog.poll()["tripped"] is True


# -- tripping ---------------------------------------------------------------


def test_watchdog_trips_with_its_own_source() -> None:
    """The audit must distinguish a watchdog trip from a user press."""
    switch = FakeSwitch()
    watchdog = watchdog_with(99.0)
    watchdog.switch = switch
    poll_until_trip(watchdog)
    assert switch.calls
    assert switch.calls[-1]["source"] == "watchdog"


def test_watchdog_uses_the_stop_scope_when_given(tmp_path: Path) -> None:
    """A watchdog trip carries the stop out, not just sets a flag."""
    switch = KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))
    scope = StopScope(switch, terminator=lambda pid, force_after: True)
    poll_until_trip(watchdog_with(99.0, scope=scope))
    assert switch.is_killed()
    assert scope.last_report["cloud_pause_reason"]


def test_watchdog_never_resets(tmp_path: Path) -> None:
    """A detector that could clear its own alarm would be no detector."""
    switch = KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))
    watchdog = watchdog_with(99.0)
    watchdog.switch = switch
    watchdog.cpu_streak_limit = 1
    watchdog.poll()
    assert switch.is_killed()
    assert not hasattr(watchdog, "reset")
    assert switch.is_killed()


def test_trip_count_is_reported() -> None:
    """Repeated trips are counted so a flapping watchdog is visible."""
    watchdog = watchdog_with(99.0, cpu_streak_limit=1)
    for _ in range(3):
        watchdog.poll()
    assert watchdog.trips == 3


def test_dead_process_does_not_count_as_saturated() -> None:
    """A process that has exited is skipped, not reported as spinning."""
    import os

    watchdog = Watchdog(FakeSwitch(), pids=[os.getpid(), 999_999])
    assert watchdog.max_cpu_percent() >= 0.0
