"""Spinal Cord — watchdog mass-deletion detection.

The second watchdog signal. Deletions are counted from the activity log rather
than by watching the filesystem, so the watchdog sees what ZUZZ did and not what
the rest of the machine did — a user tidying their Desktop must not stop the
agent.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from brain.brainstem.activity_logger import ActivityLogger
from brain.spinal_cord.watchdog import (
    DELETION_LIMIT,
    DELETION_WINDOW_SECONDS,
    Watchdog,
)
from tests.watchdog_fakes import FakeSwitch


def watching(logger: ActivityLogger) -> Watchdog:
    """Return a watchdog with an idle CPU reading and a real logger."""
    watchdog = Watchdog(FakeSwitch(), logger=logger)
    watchdog.max_cpu_percent = lambda: 1.0  # type: ignore[method-assign]
    return watchdog


def logged_deletions(logger: ActivityLogger, count: int, stamp: str | None = None) -> None:
    """Write ``count`` delete records, optionally with a fixed timestamp."""
    for index in range(count):
        extra = {"timestamp": stamp} if stamp else {}
        logger.log("body", f"delete_file_{index}", **extra)


def test_deletions_in_the_window_trip_immediately(tmp_path: Path) -> None:
    """Many deletions in one window is a loop, not a task."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logged_deletions(logger, DELETION_LIMIT + 1)
    report = watching(logger).poll()
    assert report["tripped"] is True
    assert "deletions" in report["reason"]


def test_the_window_boundary_is_not_a_trip(tmp_path: Path) -> None:
    """Exactly at the limit is still inside the safe direction."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logged_deletions(logger, DELETION_LIMIT - 1)
    assert watching(logger).poll()["tripped"] is False


def test_few_deletions_do_not_trip(tmp_path: Path) -> None:
    """Deleting a handful of files is ordinary cleanup."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logged_deletions(logger, 2)
    assert watching(logger).poll()["tripped"] is False


def test_old_deletions_are_ignored(tmp_path: Path) -> None:
    """Yesterday's mass deletion must not stop today's session."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    stale = datetime.fromtimestamp(
        time.time() - DELETION_WINDOW_SECONDS - 600, tz=timezone.utc
    ).isoformat()
    logged_deletions(logger, DELETION_LIMIT + 5, stamp=stale)
    assert watching(logger).recent_deletions() == 0


def test_non_delete_activity_is_not_counted(tmp_path: Path) -> None:
    """Chat and routing are not deletions."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    for index in range(DELETION_LIMIT + 5):
        logger.log("cerebrum", f"think_{index}")
    assert watching(logger).recent_deletions() == 0


def test_no_logger_means_no_deletion_signal() -> None:
    """Without an activity log the watchdog simply does not watch deletions."""
    assert Watchdog(FakeSwitch()).recent_deletions() == 0


def test_unparseable_timestamp_is_skipped(tmp_path: Path) -> None:
    """A malformed record is ignored rather than crashing the watchdog."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logger.log("body", "delete_file", timestamp="not a timestamp")
    assert watching(logger).recent_deletions() == 0


def test_numeric_timestamps_are_accepted(tmp_path: Path) -> None:
    """Unix timestamps are read as readily as ISO strings."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logger.log("body", "delete_file", timestamp=time.time())
    assert watching(logger).recent_deletions() == 1


def test_the_deletion_limit_is_tunable(tmp_path: Path) -> None:
    """A machine doing real bulk work can be given headroom."""
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    logged_deletions(logger, 5)
    watchdog = Watchdog(FakeSwitch(), logger=logger, deletion_limit=50)
    watchdog.max_cpu_percent = lambda: 1.0  # type: ignore[method-assign]
    assert watchdog.poll()["tripped"] is False
    assert watchdog.deletion_limit == 50
