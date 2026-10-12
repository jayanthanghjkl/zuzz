"""Multi-Agent Specialist — Idea generation and ideation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["BrainstormerAgent"]


class BrainstormerAgent(BaseAgent):
    """Specialist for idea generation and ideation.

    Produces creative concepts, alternatives, and possibilities for
    open-ended problems. Returns structured lists of ideas with rationale.
    """

    role = "brainstormer"
    description = "Idea generation & ideation"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate ideas for the given topic or problem.

        Args:
            task: The topic or problem to brainstorm.
            context: Optional context with constraints, goals, or previous ideas.

        Returns:
            Dict with keys: ideas (list), categories (dict), top_picks (list).
        """
        constraints = context.get("constraints", "") if context else ""
        goals = context.get("goals", "") if context else ""

        prompt = (
            f"Brainstorm creative ideas for the following. "
            f"Provide diverse, novel concepts with brief rationale.\n\n"
            f"Topic: {task}\n\n"
            f"Goals: {goals}\n\n"
            f"Constraints: {constraints}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "ideas": self._extract_ideas(response),
            "categories": self._categorize_ideas(response),
            "top_picks": self._extract_top_picks(response),
        }

    def _extract_ideas(self, response: str) -> list:
        lines = response.split("\n")
        ideas = [line.strip() for line in lines if line.strip().startswith(("-", "•", "*", "1.", "2.", "3.", "Idea"))]
        return ideas[:20] if ideas else [response[:500]]

    def _categorize_ideas(self, response: str) -> dict:
        # Simple categorization by keywords
        categories = {"technical": [], "creative": [], "business": [], "other": []}
        ideas = self._extract_ideas(response)
        for idea in ideas:
            idea_lower = idea.lower()
            if any(kw in idea_lower for kw in ["api", "code", "tech", "system", "data", "algorithm"]):
                categories["technical"].append(idea)
            elif any(kw in idea_lower for kw in ["design", "creative", "art", "story", "visual", "brand"]):
                categories["creative"].append(idea)
            elif any(kw in idea_lower for kw in ["market", "business", "revenue", "customer", "product", "sales"]):
                categories["business"].append(idea)
            else:
                categories["other"].append(idea)
        return categories

    def _extract_top_picks(self, response: str) -> list:
        import re
        picks = re.findall(r'(top|best|recommended|pick)[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return [p[1].strip() for p in picks][:5]