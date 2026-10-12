"""Multi-Agent Specialist — Language translation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["TranslatorAgent"]


class TranslatorAgent(BaseAgent):
    """Specialist for language translation.

    Translates content between languages while preserving meaning, tone,
    and formatting. Supports major world languages.
    """

    role = "translator"
    description = "Language translation"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Translate content to the target language.

        Args:
            task: Translation instruction (e.g., "translate to Spanish").
            context: Must contain 'content' to translate, optionally 'target_language'.

        Returns:
            Dict with keys: translated_content (str), source_language (str), target_language (str).
        """
        content = context.get("writer", "") or context.get("content", "") if context else ""
        target_lang = context.get("target_language", "Spanish") if context else "Spanish"

        if not content:
            content = task
            task = f"Translate to {target_lang}."

        prompt = (
            f"Translate the following content to {target_lang}. "
            f"Preserve meaning, tone, and any formatting.\n\n"
            f"Content:\n{content}"
        )

        response = await self.cerebrum.think(prompt, level=2)

        return {
            "translated_content": response,
            "source_language": "auto-detect",
            "target_language": target_lang,
        }