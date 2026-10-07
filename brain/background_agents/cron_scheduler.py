"""Background Agents - cron scheduling."""

from __future__ import annotations

import logging
from typing import List, Optional

from croniter import croniter
from datetime import datetime

logger = logging.getLogger(__name__)


class CronScheduler:
    """Schedule agent runs using cron expressions."""

    def __init__(self) -> None:
        self._jobs: dict = {}

    def add_job(self, agent_id: str, cron_expression: str) -> None:
        """Add a cron job for an agent.
        
        Args:
            agent_id: The agent to schedule.
            cron_expression: Cron expression (e.g. '*/15 * * * *').
        """
        base = datetime.now()
        cron = croniter(cron_expression, base)
        self._jobs[agent_id] = {
            "cron": cron_expression,
            "next_run": cron.get_next(datetime),
            "last_run": None,
        }
        logger.info("scheduled agent %s with cron: %s", agent_id, cron_expression)

    def remove_job(self, agent_id: str) -> None:
        """Remove a scheduled job."""
        self._jobs.pop(agent_id, None)

    def get_next_run(self, agent_id: str) -> Optional[datetime]:
        """Get the next scheduled run time."""
        job = self._jobs.get(agent_id)
        return job["next_run"] if job else None

    def get_due_jobs(self) -> List[str]:
        """Return agent IDs whose next_run has passed."""
        now = datetime.now()
        result = []
        for aid, job in self._jobs.items():
            next_run = job["next_run"]
            if isinstance(next_run, float):
                from datetime import datetime as dt
                next_run = dt.fromtimestamp(next_run)
            if next_run <= now:
                result.append(aid)
        return result

    def mark_run(self, agent_id: str) -> None:
        """Mark a job as run and calculate next run."""
        job = self._jobs.get(agent_id)
        if job:
            job["last_run"] = datetime.now()
            cron = croniter(job["cron"], job["last_run"])
            job["next_run"] = cron.get_next(datetime)
