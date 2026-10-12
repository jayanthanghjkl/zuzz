"""Multi-Agent Specialist — Content drafting and prose creation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["WriterAgent"]


class WriterAgent(BaseAgent):
    """Specialist for content drafting and prose creation.

    Takes research, outlines, or raw ideas and produces polished written
    content — articles, reports, documentation, emails, etc.
    """

    role = "writer"
    description = "Content drafting & prose creation"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Write content based on the task and context.

        Args:
            task: What to write (topic, outline, or instruction).
            context: Optional context with research, outlines, or previous drafts.

        Returns:
            Dict with keys: content (str), word_count (int), format (str).
        """
        research = context.get("researcher", "") if context else ""
        outline = context.get("planner", "") if context else ""

        prompt = (
            f"Write high-quality content for the following task. "
            f"Use any research or outline provided.\n\n"
            f"Task: {task}\n\n"
            f"Research: {research}\n\n"
            f"Outline: {outline}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "content": response,
            "word_count": len(response.split()),
            "format": "markdown",
        }