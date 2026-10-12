"""Multi-Agent Specialist — Document and conversation summarization."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["SummarizerAgent"]


class SummarizerAgent(BaseAgent):
    """Specialist for document and conversation summarization.

    Condenses long content into key points, executive summaries, or
    structured digests while preserving critical information.
    """

    role = "summarizer"
    description = "Document & conversation summarization"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Summarize the provided content.

        Args:
            task: Summarization instruction (e.g., "executive summary", "key points").
            context: Must contain 'content' to summarize (from writer, researcher, etc.).

        Returns:
            Dict with keys: summary (str), key_points (list), word_count (int), compression_ratio (float).
        """
        content = context.get("writer", "") or context.get("researcher", "") or context.get("content", "") if context else ""
        if not content:
            content = task
            task = "Provide a concise summary with key points."

        original_length = len(content.split())
        prompt = (
            f"Summarize the following content. {task}\n\n"
            f"Content:\n{content}"
        )

        response = await self.cerebrum.think(prompt, level=2)
        summary_length = len(response.split())

        return {
            "summary": response,
            "key_points": self._extract_key_points(response),
            "word_count": summary_length,
            "compression_ratio": round(summary_length / original_length, 2) if original_length > 0 else 0,
        }

    def _extract_key_points(self, response: str) -> list:
        lines = response.split("\n")
        points = [line.strip() for line in lines if line.strip().startswith(("-", "•", "*", "1.", "2.", "3.", "Key"))]
        return points[:10] if points else [response[:300]]