"""Multi-Agent Specialist — Timeline planning and calendar scheduling."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["SchedulerAgent"]


class SchedulerAgent(BaseAgent):
    """Specialist for timeline planning and calendar scheduling.

    Takes tasks, constraints, and preferences and produces structured
    schedules — calendars, timelines, meeting plans, availability maps.
    """

    role = "scheduler"
    description = "Timeline planning & calendar scheduling"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a schedule or timeline.

        Args:
            task: What to schedule (e.g., "project timeline", "meeting calendar").
            context: Optional context with tasks, deadlines, availability, preferences.

        Returns:
            Dict with keys: schedule (str), timeline (dict), events (list), conflicts (list).
        """
        tasks = context.get("planner", "") or context.get("tasks", "") if context else ""
        constraints = context.get("constraints", "") if context else ""
        availability = context.get("availability", "") if context else ""

        prompt = (
            f"Create a detailed schedule or timeline for the following. "
            f"Include specific dates/times, dependencies, and buffer.\n\n"
            f"Request: {task}\n\n"
            f"Tasks/Plan: {tasks}\n\n"
            f"Constraints: {constraints}\n\n"
            f"Availability: {availability}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "schedule": response,
            "timeline": self._extract_timeline(response),
            "events": self._extract_events(response),
            "conflicts": self._extract_conflicts(response),
        }

    def _extract_timeline(self, response: str) -> dict:
        import re
        timeline = {}
        for match in re.finditer(r'(week|month|day|phase)\s*(\d+)[:\-]?\s*([^\n]+)', response, re.IGNORECASE):
            timeline[f"{match.group(1)} {match.group(2)}"] = match.group(3).strip()
        return timeline

    def _extract_events(self, response: str) -> list:
        import re
        events = re.findall(r'(meeting|call|deadline|milestone|event)[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return [{"type": e[0], "details": e[1].strip()} for e in events][:20]

    def _extract_conflicts(self, response: str) -> list:
        import re
        conflicts = re.findall(r'(conflict|overlap|clash)[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return [c[1].strip() for c in conflicts][:10]