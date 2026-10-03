"""Prefrontal Cortex — Permission Gate.

Every action passes through here before the Body layer may execute it. The gate
answers three questions, in order:

1. Does an immutable rule forbid this outright?
2. What does the trust matrix say for this category and level?
3. Does the action's *target* change the answer — a delete of a temp file, a
   write into a protected directory?

What the matrix says is :mod:`brain.prefrontal_cortex.trust_matrix`'s job, and
the verdict shape is :mod:`brain.prefrontal_cortex.permission_decision`'s. This
module combines the two with the immutable rules and the path classifier.

A category the matrix does not know is refused rather than assumed harmless: the
safe direction to fail.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.trust_matrix import ASK, AUTO, BLOCKED, TrustMatrix, clamp
from brain.prefrontal_cortex.rule_mapping import blocking_rule, confirming_rule
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch
from core.paths import PROTECTED, TEMP, classify


class PermissionGate:
    """Decides whether an action may proceed.

    A threat assessment may be supplied from the Amygdala. The gate honours it
    strictly one way: a ``forbidden`` assessment blocks, and a ``dangerous`` one
    forces confirmation. Nothing the Amygdala says can turn a blocked action into
    an allowed one, or downgrade a confirmation to automatic.

    The kill switch is consulted first, before the Amygdala and before the matrix.
    While it is engaged every action is denied with no further checks — no trust
    level and no threat score can reach it, because a dialog that can be clicked
    through is not a stop.
    """

    def __init__(
        self,
        matrix: Optional[TrustMatrix] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Load the permission matrix.

        Args:
            matrix: An existing matrix to reuse, or None to read the config.
            kill_switch: The switch to consult, or None to use the singleton.
        """
        self.matrix = matrix or TrustMatrix()
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()

    def refresh(self) -> None:
        """Re-read the matrix from disk."""
        self.matrix.refresh()

    def stopped_reason(self) -> str:
        """Return why the switch is engaged, or an empty string when it is not.

        Returns:
            The recorded reason and source, for the decision's explanation.
        """
        if not self.kill_switch.is_killed():
            return ""
        status = self.kill_switch.status()
        cause = status.get("reason") or "emergency stop engaged"
        source = status.get("source") or "unknown"
        return f"kill switch engaged ({source}): {cause}"

    def row_for(self, category: ActionCategory) -> Dict[str, str]:
        """Return the per-level decisions for one action category."""
        return self.matrix.row_for(category)

    def matrix_decision(self, category: ActionCategory, level: int) -> str:
        """Return what the matrix says, ignoring the action's target."""
        return self.matrix.decision_for(category, level)

    def decide(
        self,
        action: Action,
        trust_level: int,
        threat: Optional[Any] = None,
    ) -> PermissionDecision:
        """Decide whether one action may proceed.

        Args:
            action: The proposed action.
            trust_level: Current trust level, clamped to 1-5.
            threat: Optional :class:`~brain.amygdala.threat_assessment.ThreatAssessment`.

        Returns:
            The verdict, carrying the reason for the UI and the log.
        """
        level = clamp(trust_level)

        stopped = self.stopped_reason()
        if stopped:
            return PermissionDecision.make(action, level, BLOCKED, stopped)

        if threat is not None and getattr(threat, "veto", False):
            return PermissionDecision.make(
                action,
                level,
                BLOCKED,
                f"amygdala scored this {getattr(threat, 'level', 'forbidden')} "
                f"({getattr(threat, 'score', '?')}/10): {getattr(threat, 'explanation', '')}",
            )

        forbidden = blocking_rule(action)
        if forbidden:
            return PermissionDecision.make(
                action, level, BLOCKED, f"immutable rule {forbidden}", forbidden
            )

        base = self.matrix.decision_for(action.category, level)
        if base == BLOCKED:
            return PermissionDecision.make(action, level, BLOCKED, "blocked at every trust level")

        override = self._target_override(action, level)
        if override:
            base = override
            reason = self._override_reason(action, level)
        else:
            reason = self._reason_for(action, level, base)

        if base == AUTO and threat is not None and getattr(threat, "requires_confirmation", False):
            # Trust level 5 is autonomy over routine work, not permission to do
            # dangerous things silently.
            base = ASK
            reason = f"{reason}, but the amygdala scored this {threat.level} ({threat.score}/10)"

        decision = PermissionDecision.make(action, level, base, reason)
        rule = confirming_rule(action)
        if rule:
            return PermissionDecision.make(
                action,
                level,
                decision.decision,
                f"{decision.reason} (immutable rule {rule})",
                rule,
            )
        return decision

    def _target_override(self, action: Action, level: int) -> Optional[str]:
        """Return a decision forced by the action's target.

        Only two cases exist, both from the approved decisions: a delete of a
        temp file may auto-approve from level 3, and a write to a protected path
        is blocked outright.

        Args:
            action: The proposed action.
            level: Current trust level.

        Returns:
            A decision string, or None to leave the matrix's answer alone.
        """
        if not action.target:
            return None
        kind = classify(action.target)
        if action.category == ActionCategory.DELETE_FILES and kind == TEMP:
            if level >= self.matrix.delete_temp_auto_from_level():
                return AUTO
        if action.category == ActionCategory.WRITE_FILES and kind == PROTECTED:
            if self.matrix.blocks_protected_writes():
                return BLOCKED
        return None

    def _override_reason(self, action: Action, level: int) -> str:
        """Explain a target-based override in one line."""
        kind = classify(action.target) if action.target else ""
        if action.category == ActionCategory.DELETE_FILES and kind == TEMP:
            return f"temp file deletion is auto-approved from trust level {level}"
        return f"target is a {kind} path, which is never writable"

    def _reason_for(self, action: Action, level: int, decision: str) -> str:
        """Explain a matrix decision in one line."""
        if decision == AUTO:
            return f"trust level {level} auto-approves {action.category}"
        return f"trust level {level} requires confirmation for {action.category}"