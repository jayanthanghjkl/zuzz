"""Last-boundary permission verification shared by voice components."""

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import get_kill_switch


def verify_voice(decision: PermissionDecision, category: str, switch=None) -> None:
    """Reject unapproved, dry-run, wrong-category or stopped audio operations."""
    if (switch or get_kill_switch()).is_killed():
        raise ValueError("Voice operation refused: kill switch engaged")
    if not isinstance(decision, PermissionDecision) or not decision.allowed or decision.dry_run:
        raise ValueError("Voice operation requires an allowed permission decision")
    if decision.action.category != category:
        raise ValueError("Voice permission was issued for a different operation category")
