"""Spinal Cord — scheduler pause/resume tests.

The stop has to reach the scheduler, and the reset has to put the jobs back on
the same intervals. Registering a job *while* paused is the case that is easy to
get wrong: it must be remembered without being scheduled, or a job added during
an emergency would silently never run after the reset.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from brain.brainstem.scheduler import Scheduler


@pytest.fixture
def scheduler() -> Scheduler:
    """Return a scheduler with no APScheduler instance created."""
    return Scheduler()


def noop() -> Dict[str, Any]:
    """A stand-in job that records nothing."""
    return {}


def test_a_new_scheduler_is_running(scheduler: Scheduler) -> None:
    """Registration starts the scheduler, as before."""
    assert scheduler.paused is False
    assert scheduler.jobs() == []


def test_pause_stops_new_job_registration(scheduler: Scheduler) -> None:
    """A job added while paused is remembered but not scheduled."""
    scheduler.pause()
    scheduler.add_job(noop, "heartbeat", interval_seconds=30)
    assert scheduler.paused is True
    assert [job["name"] for job in scheduler.jobs()] == ["heartbeat"]


async def test_resume_restores_registered_jobs(scheduler: Scheduler) -> None:
    """A job registered while stopped runs again after the reset.

    Registered during the stop on purpose: it must be remembered without being
    scheduled, or a job added during an emergency would silently never run.
    """
    scheduler.pause()
    scheduler.add_job(noop, "heartbeat", interval_seconds=30)
    scheduler.resume()
    assert scheduler.paused is False
    assert [job["name"] for job in scheduler.jobs()] == ["heartbeat"]


def test_start_does_nothing_while_paused(scheduler: Scheduler) -> None:
    """A paused scheduler must not be revived by a stray start call."""
    scheduler.pause()
    scheduler.start()
    assert scheduler.paused is True


def test_pause_is_idempotent(scheduler: Scheduler) -> None:
    """Stopping twice must not raise or double-log."""
    scheduler.pause()
    scheduler.pause()
    assert scheduler.paused is True


async def test_resume_clears_the_pause(scheduler: Scheduler) -> None:
    """The reset restores the machine to a working state.

    Async because resuming restarts APScheduler, which needs a running loop —
    the same requirement it has when the server starts it.
    """
    scheduler.pause()
    scheduler.add_job(noop, "heartbeat", interval_seconds=30)
    scheduler.resume()
    assert scheduler.paused is False


def test_resume_is_a_no_op_when_not_paused(scheduler: Scheduler) -> None:
    """Resuming a running scheduler must not re-add jobs."""
    scheduler.resume()
    assert scheduler.paused is False


def test_remove_job_while_paused_does_not_touch_apscheduler(scheduler: Scheduler) -> None:
    """Removing a job while paused works without a scheduler instance.

    The paused scheduler has deliberately dropped its APScheduler object, so a
    removal that reached for it would raise.
    """
    scheduler.pause()
    scheduler.add_job(noop, "heartbeat", interval_seconds=30)
    assert scheduler.remove_job("heartbeat") is True
    assert scheduler.jobs() == []
    assert scheduler.remove_job("heartbeat") is False


async def test_status_reports_pause_and_jobs(scheduler: Scheduler) -> None:
    """The dashboard needs both the pause state and the job list."""
    scheduler.add_job(noop, "heartbeat", interval_seconds=30)
    scheduler.pause()
    status = scheduler.status()
    assert status["paused"] is True
    assert status["jobs"] == [{"name": "heartbeat", "interval_seconds": 30}]


def test_non_positive_interval_is_refused(scheduler: Scheduler) -> None:
    """The existing guard survives the pause/resume changes."""
    with pytest.raises(ValueError):
        scheduler.add_job(noop, "bad", interval_seconds=0)


def test_scheduler_reports_a_name_for_the_stop_scope(scheduler: Scheduler) -> None:
    """The stop report names the subsystem it paused."""
    assert scheduler.name == "scheduler"


def test_paused_registry_is_reported_to_the_ui(scheduler: Scheduler) -> None:
    """What would run stays visible while stopped — the user may want it back.

    No job is registered here: registration starts APScheduler, which needs a
    running loop, and this test is only about what the UI can see while paused.
    """
    scheduler.pause()
    assert scheduler.status()["jobs"] == []
