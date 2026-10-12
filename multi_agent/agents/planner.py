"""Multi-Agent Specialist — Strategic roadmapping and task planning."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["PlannerAgent"]


class PlannerAgent(BaseAgent):
    """Specialist for strategic roadmapping and task planning.

    Takes goals, constraints, and context and produces structured plans —
    roadmaps, timelines, milestones, step-by-step approaches.
    """

    role = "planner"
    description = "Strategic roadmapping & task planning"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a plan or roadmap for the given task.

        Args:
            task: The goal or project to plan.
            context: Optional context with research, constraints, resources.

        Returns:
            Dict with keys: plan (str), steps (list), timeline (dict), milestones (list).
        """
        research = context.get("researcher", "") if context else ""
        constraints = context.get("constraints", "") if context else ""

        prompt = (
            f"Create a detailed, actionable plan for the following. "
            f"Include steps, timeline, milestones, and resource considerations.\n\n"
            f"Goal: {task}\n\n"
            f"Research/Context: {research}\n\n"
            f"Constraints: {constraints}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "plan": response,
            "steps": self._extract_steps(response),
            "timeline": self._extract_timeline(response),
            "milestones": self._extract_milestones(response),
        }

    def _extract_steps(self, response: str) -> list:
        lines = response.split("\n")
        steps = [line.strip() for line in lines if line.strip().startswith(("-", "•", "*", "1.", "2.", "3.", "Step"))]
        return steps[:20] if steps else [response[:500]]

    def _extract_timeline(self, response: str) -> dict:
        import re
        timeline = {}
        for match in re.finditer(r'(week|month|day|phase)\s*(\d+)[:\-]?\s*([^\n]+)', response, re.IGNORECASE):
            timeline[f"{match.group(1)} {match.group(2)}"] = match.group(3).strip()
        return timeline

    def _extract_milestones(self, response: str) -> list:
        import re
        milestones = re.findall(r'milestone[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return milestones[:10]