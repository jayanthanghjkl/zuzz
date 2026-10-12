"""Multi-Agent Specialist — Data analysis and evaluation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["AnalystAgent"]


class AnalystAgent(BaseAgent):
    """Specialist for data analysis and evaluation.

    Takes data, research findings, or complex information and produces
    structured analysis — comparisons, evaluations, interpretations, metrics.
    """

    role = "analyst"
    description = "Data analysis & evaluation"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Analyze data or information and return structured analysis.

        Args:
            task: What to analyze (data, findings, options, etc.).
            context: Optional context with research, data, or previous analysis.

        Returns:
            Dict with keys: analysis (str), key_insights (list), metrics (dict).
        """
        research = context.get("researcher", "") if context else ""
        data = context.get("data", "") if context else ""

        prompt = (
            f"Analyze the following thoroughly. Provide key insights, "
            f"comparisons, and measurable metrics where applicable.\n\n"
            f"Task: {task}\n\n"
            f"Research/Context: {research}\n\n"
            f"Data: {data}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "analysis": response,
            "key_insights": self._extract_insights(response),
            "metrics": self._extract_metrics(response),
        }

    def _extract_insights(self, response: str) -> list:
        lines = response.split("\n")
        insights = [line.strip() for line in lines if line.strip().startswith(("-", "•", "*", "→", "⇒"))]
        return insights[:10] if insights else [response[:300]]

    def _extract_metrics(self, response: str) -> dict:
        import re
        metrics = {}
        for match in re.finditer(r'(\w+):\s*([\d.]+[%$]?)', response):
            metrics[match.group(1)] = match.group(2)
        return metrics