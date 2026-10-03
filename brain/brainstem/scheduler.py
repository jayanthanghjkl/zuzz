"""Brainstem — Scheduler.

APScheduler wrapper for recurring background work (heartbeats, model cleanup,
habit triggers). Jobs are registered at startup and survive for the life of the
process.

The scheduler can be paused by the Spinal Cord's kill switch. Pausing is not the
same as shutting down: the job registry is kept so resuming restores the same
jobs on the same intervals, and so the dashboard can still show what *would* run
while ZUZZ is stopped.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from brain.brainstem.activity_logger import get_logger


class Scheduler:
    """Owns the background job scheduler.

    Attributes:
        _scheduler: The underlying APScheduler instance, created lazily.
        paused: True while the kill switch has stopped background work.
    """

    name = "scheduler"

    def __init__(self) -> None:
        """Initialise the scheduler without starting it."""
        self._scheduler = None
        self.logger = get_logger()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._callables: Dict[str, Callable[..., Any]] = {}
        self.paused = False

    def _ensure(self):
        """Create the APScheduler instance on first use.

        Returns:
            An AsyncIOScheduler.

        Raises:
            RuntimeError: If APScheduler is not installed.
        """
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            self._scheduler = AsyncIOScheduler()
        return self._scheduler

    def start(self) -> None:
        """Start the scheduler if it is not running and not paused."""
        if self.paused:
            return
        scheduler = self._ensure()
        if not scheduler.running:
            scheduler.start()
            self.logger.log("brainstem", "scheduler_start")

    def shutdown(self) -> None:
        """Stop the scheduler and forget it, so a later start builds a new one.

        Used at process shutdown. The job registry is kept, so :meth:`resume`
        can put the jobs back.
        """
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self.logger.log("brainstem", "scheduler_stop")
        self._scheduler = None

    def pause(self) -> None:
        """Stop running background jobs because the kill switch is engaged.

        The jobs stay registered: the user may have stopped ZUZZ because one job
        was misbehaving, not because every job is unwanted.
        """
        if self.paused:
            return
        self.paused = True
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self.logger.log("brainstem", "scheduler_pause", reason="kill switch")

    def resume(self) -> None:
        """Restart paused jobs after the user reset the kill switch."""
        if not self.paused:
            return
        self.paused = False
        for name, meta in self._jobs.items():
            func = self._callables.get(name)
            if func is None:
                continue
            try:
                self._ensure().add_job(
                    func,
                    "interval",
                    seconds=meta["interval_seconds"],
                    id=name,
                    replace_existing=True,
                )
            except Exception:  # one bad job must not block the rest
                continue
        self.start()
        self.logger.log("brainstem", "scheduler_resume", jobs=len(self._jobs))

    def add_job(
        self,
        func: Callable[..., Any],
        name: str,
        interval_seconds: int,
    ) -> str:
        """Register a recurring job.

        Registering while paused records the job without scheduling it, so
        :meth:`resume` picks it up.

        Args:
            func: Zero-argument or async callable to run.
            name: Unique job name; re-adding the same name replaces it.
            interval_seconds: Seconds between runs.

        Returns:
            The job name.

        Raises:
            ValueError: If ``interval_seconds`` is not positive.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._jobs[name] = {"interval_seconds": interval_seconds}
        self._callables[name] = func
        if not self.paused:
            self.start()
            self._ensure().add_job(
                func, "interval", seconds=interval_seconds, id=name, replace_existing=True
            )
        self.logger.log("brainstem", "job_added", job=name, interval=interval_seconds)
        return name

    def remove_job(self, name: str) -> bool:
        """Remove a registered job.

        Args:
            name: The job name.

        Returns:
            True when a job was removed.
        """
        removed = self._jobs.pop(name, None) is not None
        self._callables.pop(name, None)
        if removed and not self.paused:
            try:
                self._ensure().remove_job(name)
            except Exception:
                pass
        return removed

    def jobs(self) -> List[Dict[str, Any]]:
        """Return the registered jobs for the dashboard."""
        return [{"name": name, **meta} for name, meta in self._jobs.items()]

    def status(self) -> Dict[str, Any]:
        """Return scheduler state for ``/api/brain/status``."""
        return {
            "paused": self.paused,
            "running": bool(self._scheduler and self._scheduler.running),
            "jobs": self.jobs(),
        }
