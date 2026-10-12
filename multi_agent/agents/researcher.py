"""Multi-Agent Specialist — Web research and information retrieval."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["ResearcherAgent"]


class ResearcherAgent(BaseAgent):
    """Specialist for web research and information gathering.

    Uses the Cerebrum's Corpus Callosum to search the web, fetch pages,
    and synthesize findings. Returns structured research notes with sources.
    """

    role = "researcher"
    description = "Web research & information retrieval"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Research a topic and return structured findings.

        Args:
            task: The research question or topic.
            context: Optional context (e.g., previous findings, constraints).

        Returns:
            Dict with keys: findings (list), sources (list), summary (str).
        """
        # Use the cerebrum's think method with a research-focused prompt
        prompt = (
            f"Research the following topic thoroughly. Provide key findings, "
            f"reliable sources, and a concise summary.\n\nTopic: {task}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "findings": self._extract_findings(response),
            "sources": self._extract_sources(response),
            "summary": self._extract_summary(response),
            "raw_response": response,
        }

    def _extract_findings(self, response: str) -> list:
        """Extract structured findings from the response."""
        # Simple extraction - in practice could be more sophisticated
        lines = response.split("\n")
        findings = [line.strip() for line in lines if line.strip().startswith(("-", "•", "*", "1.", "2.", "3."))]
        return findings[:10] if findings else [response[:500]]

    def _extract_sources(self, response: str) -> list:
        """Extract source references from the response."""
        import re
        urls = re.findall(r'https?://[^\s]+', response)
        return list(set(urls))[:10]

    def _extract_summary(self, response: str) -> str:
        """Extract or generate a summary."""
        sentences = response.split(".")
        return ". ".join(sentences[:3]) + "." if len(sentences) > 3 else response