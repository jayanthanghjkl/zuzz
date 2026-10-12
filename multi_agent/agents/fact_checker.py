"""Multi-Agent Specialist — Fact checking and claim validation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from multi_agent.agents.base import BaseAgent

__all__ = ["FactCheckerAgent"]


class FactCheckerAgent(BaseAgent):
    """Specialist for fact checking and claim validation.

    Verifies claims, checks accuracy, and validates information against
    known sources. Returns confidence assessments and corrections.
    """

    role = "fact_checker"
    description = "Fact checking & claim validation"

    async def execute(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Fact-check claims in the provided content.

        Args:
            task: What to fact-check (e.g., "verify all claims", "check statistics").
            context: Must contain 'content' to verify (from writer, researcher, etc.).

        Returns:
            Dict with keys: verification (str), claims_checked (list), corrections (list), confidence (float).
        """
        content = context.get("writer", "") or context.get("researcher", "") or context.get("content", "") if context else ""
        if not content:
            content = task
            task = "Verify all factual claims in this content."

        prompt = (
            f"Fact-check the following content. {task} "
            f"Identify each claim, assess its accuracy, and note any corrections needed.\n\n"
            f"Content:\n{content}"
        )

        response = await self.cerebrum.think(prompt, level=3)

        return {
            "verification": response,
            "claims_checked": self._extract_claims(response),
            "corrections": self._extract_corrections(response),
            "confidence": self._estimate_confidence(response),
        }

    def _extract_claims(self, response: str) -> list:
        import re
        claims = re.findall(r'claim[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return claims[:10]

    def _extract_corrections(self, response: str) -> list:
        import re
        corrections = re.findall(r'(correction|incorrect|wrong|false)[:\-]\s*([^\n]+)', response, re.IGNORECASE)
        return [c[1].strip() for c in corrections][:10]

    def _estimate_confidence(self, response: str) -> float:
        # Simple heuristic based on language
        confident_words = ["verified", "accurate", "correct", "true", "confirmed"]
        uncertain_words = ["unclear", "uncertain", "disputed", "unverified", "questionable"]
        response_lower = response.lower()
        confident = sum(response_lower.count(w) for w in confident_words)
        uncertain = sum(response_lower.count(w) for w in uncertain_words)
        total = confident + uncertain
        return round(confident / total, 2) if total > 0 else 0.5