"""Spinal Cord — Stop Scope.

Engaging the flag is only half the job. An emergency stop that leaves a runaway
scheduled job running has not stopped anything, so this module performs the
stop: it pauses scheduled jobs, terminates registered background processes and
flags the cloud agents that cannot be stopped from here.

Scope, from the approved decision Q2:

* **Local interactive actions** — stopped by the gate, which denies everything
  while the flag is set. Nothing needs doing here.
* **Local scheduled jobs** — paused, because a runaway job may be the cause and
  leaving it running would re-trip the switch forever.
* **Local background processes** — terminated.
* **Cloud agents** — *cannot* be stopped remotely, so they are flagged. The
  bridge reports every provider as kill-switch-active and the Colab notebook
  reads the flag on its next sync and pauses.
* **Reset** — manual only, never automatic.

The module holds no policy: it is handed the things to stop and stops them, so
it can be exercised in tests without a scheduler, a process table or a network.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from brain.spinal_cord.stop_state import SOURCE_UI

#: Reported by the bridge and the UI for every cloud provider while stopped.
CLOUD_PAUSE_REASON = "kill switch active"

#: Provider state shown in ``/api/providers`` while the switch is engaged.
KILL_SWITCH_ACTIVE = "kill_switch_active"


class Pausable(Protocol):
    """Anything the stop can pause, such as the Brainstem scheduler."""

    def pause(self) -> None:  # pragma: no cover - protocol definition
        """Stop running background work."""
        ...


class StopScope:
    """Carries out an emergency stop across the running system.

    Attributes:
        switch: The kill switch whose engagement this scope acts on.
        pausables: Objects exposing ``pause()``, e.g. the scheduler.
        processes: PIDs of background processes ZUZZ started.
        cloud_agents: Names of cloud agents that can only be flagged.
    """

    def __init__(
        self,
        switch: Any,
        *,
        pausables: Optional[Iterable[Pausable]] = None,
        processes: Optional[Iterable[int]] = None,
        cloud_agents: Optional[Iterable[str]] = None,
        terminator: Optional[Callable[[int, float], bool]] = None,
    ) -> None:
        """Collect the things a stop should reach.

        Args:
            switch: The :class:`~brain.spinal_cord.kill_switch.KillSwitch`.
            pausables: Objects with a ``pause()`` method.
            processes: PIDs of ZUZZ-spawned background processes.
            cloud_agents: Cloud provider names that must be flagged.
            terminator: Callable used to stop a process; overridden in tests.
        """
        self.switch = switch
        self.pausables: List[Pausable] = list(pausables or [])
        self.processes: List[int] = list(processes or [])
        self.cloud_agents: List[str] = list(cloud_agents or [])
        self.terminator = terminator
        self.last_report: Dict[str, Any] = {}

    def register_process(self, pid: int) -> None:
        """Record a background process the stop should terminate.

        Args:
            pid: Process id ZUZZ started.
        """
        if pid not in self.processes:
            self.processes.append(pid)

    def engage(self, reason: str = "", source: str = SOURCE_UI) -> Dict[str, Any]:
        """Trip the switch and carry out the stop.

        Args:
            reason: What caused the stop, recorded in the audit log.
            source: Where the stop came from.

        Returns:
            A report of what was stopped, what was flagged, and anything that
            could not be stopped.
        """
        status = self.switch.trigger(source=source, reason=reason)
        self.last_report = {
            "paused": self.pause_all(),
            "terminated": self.terminate_all(),
            "cloud_flagged": list(self.cloud_agents),
            "cloud_pause_reason": CLOUD_PAUSE_REASON,
            "status": status,
        }
        return self.last_report

    def pause_all(self) -> List[str]:
        """Pause every registered background job source.

        Returns:
            Names of the pausables that were paused. A pauser that raises is
            skipped: one broken subsystem must not abort the emergency stop.
        """
        paused: List[str] = []
        for item in self.pausables:
            name = getattr(item, "name", None) or type(item).__name__
            try:
                item.pause()
            except Exception:  # noqa: BLE001 - a stop must not be interruptible
                continue
            paused.append(str(name))
        return paused

    def terminate_all(self, force_after: float = 2.0) -> List[int]:
        """Terminate every registered background process.

        Args:
            force_after: Seconds before escalating from a polite request to a
                hard kill, matching the two-second guarantee in the plan.

        Returns:
            The PIDs confirmed gone. Anything that survived is omitted, and the
            caller can see it by comparing against ``self.processes``.
        """
        if self.terminator is not None:
            return [pid for pid in self.processes if self.terminator(pid, force_after)]
        from brain.spinal_cord.terminate import terminate_brain

        return [pid for pid in self.processes if terminate_brain(pid, force_after)]


def stop_report(scope: StopScope) -> Dict[str, Any]:
    """Return the last report from a scope, for the UI after a restart."""
    return dict(scope.last_report)
