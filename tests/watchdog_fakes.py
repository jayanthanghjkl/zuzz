"""Watchdog test doubles.

Shared by the CPU-signal tests and the mass-deletion tests, which would otherwise
each carry their own copy of the same two helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List

from brain.spinal_cord.watchdog import Watchdog


class FakeSwitch:
    """Records trigger calls without touching the filesystem.

    Lets a watchdog be tested for *what it decided* without writing a stop flag
    into the developer's real storage.
    """

    def __init__(self) -> None:
        """Start un-tripped."""
        self.calls: List[Dict[str, Any]] = []

    def trigger(self, source: str = "ui", reason: str = "", last_action: str = "") -> Dict[str, Any]:
        """Record a trip."""
        self.calls.append({"source": source, "reason": reason})
        return {"engaged": True, "reason": reason}

    def status(self) -> Dict[str, Any]:
        """Report the switch as engaged."""
        return {"engaged": True}


def watchdog_with(fake_cpu: float, **kwargs: Any) -> Watchdog:
    """Return a watchdog whose CPU reading is fixed.

    Args:
        fake_cpu: The percentage every poll should report.
        **kwargs: Passed through to :class:`~brain.spinal_cord.watchdog.Watchdog`.

    Returns:
        A watchdog that reads ``fake_cpu`` instead of sampling the process table.
    """
    watchdog = Watchdog(FakeSwitch(), **kwargs)
    watchdog.max_cpu_percent = lambda: fake_cpu  # type: ignore[method-assign]
    return watchdog


def poll_until_trip(watchdog: Watchdog, limit: int = 20) -> Dict[str, Any]:
    """Poll until the watchdog trips, returning the report that tripped it.

    Args:
        watchdog: The watchdog to poll.
        limit: How many polls to allow before giving up.

    Returns:
        The first report with ``tripped`` set.

    Raises:
        AssertionError: If the watchdog never trips within ``limit`` polls.
    """
    for _ in range(limit):
        report = watchdog.poll()
        if report["tripped"]:
            return report
    raise AssertionError("watchdog never tripped")
