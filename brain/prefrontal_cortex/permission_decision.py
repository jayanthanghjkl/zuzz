"""Prefrontal Cortex — Permission Decision.

The gate's verdict on one action, as data. Separate from the gate so the Body
layer can accept and check a decision without importing the rules that produced
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import Action
from brain.prefrontal_cortex.trust_matrix import AUTO, BLOCKED

ASK = "ask"


@dataclass(frozen=True)
class PermissionDecision:
    """The gate's verdict on one action.

    Attributes:
        action: The action that was decided.
        allowed: True when the Body layer may execute it.
        requires_confirmation: True when a human must approve first.
        decision: ``auto``, ``ask`` or ``blocked``.
        reason: Human-readable explanation, shown in the UI and the log.
        trust_level: The level the decision was made at.
        dry_run: Mirrored from the action, so the Body layer cannot guess.
        rule_id: Immutable rule that fired, when one did.
    """

    action: Action
    allowed: bool
    requires_confirmation: bool
    decision: str
    reason: str
    trust_level: int
    dry_run: bool = False
    rule_id: Optional[str] = None

    @property
    def blocked(self) -> bool:
        """Return True when no trust level could ever allow this action."""
        return self.decision == BLOCKED

    @classmethod
    def make(
        cls,
        action: Action,
        level: int,
        decision: str,
        reason: str,
        rule_id: Optional[str] = None,
    ) -> "PermissionDecision":
        """Build a decision from a verdict, deriving the boolean flags.

        Args:
            action: The action being decided.
            level: Trust level the decision was made at.
            decision: ``auto``, ``ask`` or ``blocked``.
            reason: Explanation for the UI and the log.
            rule_id: Immutable rule that fired, when one did.

        Returns:
            The decision object, with ``allowed`` and ``requires_confirmation`
            consistent with ``decision``.
        """
        return cls(
            action=action,
            allowed=decision == AUTO,
            requires_confirmation=decision == ASK,
            decision=decision,
            reason=reason,
            trust_level=level,
            dry_run=action.dry_run,
            rule_id=rule_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API and the log."""
        return {
            "category": str(self.action.category),
            "target": self.action.target,
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "decision": self.decision,
            "reason": self.reason,
            "trust_level": self.trust_level,
            "dry_run": self.dry_run,
            "rule_id": self.rule_id,
            "description": self.action.describe(),
        }