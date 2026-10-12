"""Cerebellum — self-critique.

Runs a lightweight critique pass on the model's reply before it reaches the
user. Catches common issues: hallucinated facts, ungrounded claims, missing
caveats, overconfidence, and style violations.

The critic does not rewrite — it flags. The Cerebrum can choose to regenerate
or attach the flags as metadata for the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

__all__ = [
    "SelfCritic",
    "CritiqueResult",
    "CritiqueFlag",
    "critique_reply",
]

#: Patterns that suggest a claim may be ungrounded or hallucinated.
_UNGROUNDED_PATTERNS = [
    r"\b(as an AI|as a language model|I don't have access|I cannot)\b",
    r"\bI (?:am not|can't|cannot) (?:sure|certain|verify|confirm)\b",
    r"\baccording to (?:my knowledge|my training)\b",
    r"\bI (?:believe|think) (?:the|this|that)\b",
]

#: Patterns suggesting overconfidence without basis.
_OVERCONFIDENCE_PATTERNS = [
    r"\b(?:definitely|certainly|absolutely|guaranteed) (?:will|works?|is|has)\b",
    r"\bthere is no (?:way|chance|possibility)\b",
    r"\bthis will (?:definitely|certainly|always) work\b",
]

#: Patterns suggesting missing caveats for risky operations.
_RISKY_OPERATIONS = [
    (r"\b(?:delete|remove|drop|truncate)\b", "destructive operation"),
    (r"\b(?:sudo|admin|root|elevated)\b", "privileged operation"),
    (r"\b(?:production|prod|live)\b", "production environment"),
    (r"\b(?:format|wipe|erase)\b", "data loss risk"),
    (r"\b(?:install|pip install|npm install)\b", "software installation"),
]

#: Style violations based on Cerebellum config.
_STYLE_VIOLATIONS = [
    (r"^(?:Sure|Certainly|Of course|Absolutely|No problem|Got it|Understood)[.!]", "filler opener"),
    (r"I (?:am |')?sorry (?:to hear that|for the|about)", "apology preamble"),
    (r"I understand (?:that |)?(?:this is|it is)", "empathy preamble"),
]

#: Required caveats for certain topics.
_REQUIRED_CAVEATS: Dict[str, str] = {
    "security": "Never expose secrets or credentials in code.",
    "production": "Test in staging before deploying to production.",
    "data_loss": "Back up data before destructive operations.",
    "privilege": "Use least privilege; avoid running as root/admin.",
}


@dataclass
class CritiqueFlag:
    """One issue found during self-critique.

    Attributes:
        code: Machine-readable flag code.
        message: Human-readable description.
        severity: "info", "warn", or "error".
        span: Optional (start, end) character indices in the reply.
    """
    code: str
    message: str
    severity: str
    span: Optional[tuple[int, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "span": self.span,
        }


@dataclass
class CritiqueResult:
    """Complete critique of a reply.

    Attributes:
        flags: All issues found.
        has_blocking: Whether any flag is severity "error".
        summary: One-line summary for logging.
    """
    flags: List[CritiqueFlag]
    has_blocking: bool
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flags": [f.to_dict() for f in self.flags],
            "has_blocking": self.has_blocking,
            "summary": self.summary,
        }


class SelfCritic:
    """Critiques a model reply for common issues."""

    def __init__(self) -> None:
        pass

    def critique(
        self,
        reply: str,
        question: str = "",
        context: str = "",
    ) -> CritiqueResult:
        """Run all critique checks on a reply."""
        flags: List[CritiqueFlag] = []

        # 1. Ungrounded claims
        flags.extend(self._check_ungrounded(reply))

        # 2. Overconfidence
        flags.extend(self._check_overconfidence(reply))

        # 3. Risky operations without caveats
        flags.extend(self._check_risky_operations(reply))

        # 4. Style violations
        flags.extend(self._check_style(reply))

        # 5. Missing required caveats
        flags.extend(self._check_missing_caveats(reply, question))

        # 6. Contradiction with context (if provided)
        if context:
            flags.extend(self._check_contradiction(reply, context))

        has_blocking = any(f.severity == "error" for f in flags)
        summary = self._summarize(flags)

        return CritiqueResult(flags=flags, has_blocking=has_blocking, summary=summary)

    def _check_ungrounded(self, reply: str) -> List[CritiqueFlag]:
        flags = []
        for pattern in _UNGROUNDED_PATTERNS:
            for match in re.finditer(pattern, reply, re.IGNORECASE):
                flags.append(CritiqueFlag(
                    code="UNGROUNDED_CLAIM",
                    message="Reply contains language suggesting ungrounded knowledge",
                    severity="warn",
                    span=(match.start(), match.end()),
                ))
        return flags

    def _check_overconfidence(self, reply: str) -> List[CritiqueFlag]:
        flags = []
        for pattern in _OVERCONFIDENCE_PATTERNS:
            for match in re.finditer(pattern, reply, re.IGNORECASE):
                flags.append(CritiqueFlag(
                    code="OVERCONFIDENCE",
                    message="Absolute certainty expressed without qualification",
                    severity="warn",
                    span=(match.start(), match.end()),
                ))
        return flags

    def _check_risky_operations(self, reply: str) -> List[CritiqueFlag]:
        flags = []
        for pattern, risk_type in _RISKY_OPERATIONS:
            for match in re.finditer(pattern, reply, re.IGNORECASE):
                # Check if a caveat appears nearby (within 200 chars)
                nearby = reply[max(0, match.start()-200):match.end()+200].lower()
                caveat_words = ["backup", "caution", "warning", "careful", "test", "staging", "review"]
                if not any(cw in nearby for cw in caveat_words):
                    flags.append(CritiqueFlag(
                        code="MISSING_CAVEAT",
                        message=f"Risky operation ({risk_type}) mentioned without cautionary language",
                        severity="warn",
                        span=(match.start(), match.end()),
                    ))
        return flags

    def _check_style(self, reply: str) -> List[CritiqueFlag]:
        flags = []
        for pattern, violation in _STYLE_VIOLATIONS:
            for match in re.finditer(pattern, reply, re.IGNORECASE):
                flags.append(CritiqueFlag(
                    code="STYLE_VIOLATION",
                    message=f"Style violation: {violation}",
                    severity="info",
                    span=(match.start(), match.end()),
                ))
        return flags

    def _check_missing_caveats(self, reply: str, question: str) -> List[CritiqueFlag]:
        flags = []
        combined = (question + " " + reply).lower()
        for topic, caveat in _REQUIRED_CAVEATS.items():
            if topic in combined:
                # Check if caveat or similar language is present
                caveat_keywords = caveat.lower().split()
                if not any(kw in reply.lower() for kw in caveat_keywords if len(kw) > 3):
                    flags.append(CritiqueFlag(
                        code="MISSING_CAVEAT",
                        message=f"Topic '{topic}' discussed but caveat missing: {caveat}",
                        severity="info",
                    ))
        return flags

    def _check_contradiction(self, reply: str, context: str) -> List[CritiqueFlag]:
        # Simple contradiction check: look for direct negations of context claims
        flags = []
        # This is a placeholder for more sophisticated NLI
        # For now, just flag if reply says "never" or "always" about something context says otherwise
        if "never" in reply.lower() and "always" in context.lower():
            flags.append(CritiqueFlag(
                code="POSSIBLE_CONTRADICTION",
                message="Reply uses absolute negation where context suggests otherwise",
                severity="info",
            ))
        return flags

    def _summarize(self, flags: List[CritiqueFlag]) -> str:
        if not flags:
            return "No issues found"
        counts: Dict[str, int] = {}
        for f in flags:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        parts = [f"{v} {k}" for k, v in counts.items()]
        return f"Critique: {', '.join(parts)}"


def critique_reply(
    reply: str,
    question: str = "",
    context: str = "",
) -> CritiqueResult:
    """Convenience function to critique a reply."""
    critic = SelfCritic()
    return critic.critique(reply, question, context)