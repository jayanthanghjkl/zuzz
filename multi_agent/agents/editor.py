"""Multi-Agent Specialist — Text refinement, proofreading and grammar."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["EditorAgent"]


class EditorAgent(BaseAgent):
    """Specialist for text refinement, proofreading and grammar.

    Takes written content and improves clarity, correctness, style, and
    readability. Returns the edited version with change notes.
    """

    role = "editor"
    description = "Text refinement, proofreading & grammar"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Edit and refine the provided content.

        Args:
            task: Editing instruction (e.g., "improve clarity", "fix grammar").
            context: Must contain 'content' from a writer or other source.

        Returns:
            Dict with keys: edited_content (str), changes (list), word_count (int).
        """
        content = context.get("writer", "") or context.get("content", "") if context else ""
        if not content:
            content = task
            task = "Improve clarity, grammar, and flow of this content."

        prompt = (
            f"Edit the following content. {task}\n\n"
            f"Content:\n{content}\n\n"
            f"Return the edited version and a brief list of key changes made."
        )

        response = await self.cerebrum.think(prompt, level=2)

        return {
            "edited_content": response,
            "changes": self._extract_changes(response),
            "word_count": len(response.split()),
        }

    def _extract_changes(self, response: str) -> list:
        lines = response.split("\n")
        changes = [line.strip() for line in lines if "change" in line.lower() or "fix" in line.lower() or "improve" in line.lower()]
        return changes[:10] if changes else ["General editing and refinement applied"]