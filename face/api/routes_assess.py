"""Face API — threat assessment routes.

Scoring an action for danger, and the safety audit trail. Split from
`routes_safety.py` so the trust-level endpoints stay readable.

Nothing here executes an action. `/api/safety/assess` reports; the Body layer is
not wired up yet, and when it is, this endpoint still only ever returns a verdict.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.immutable_rules import RULES
from brain.prefrontal_cortex.permission_gate import PermissionGate
from core.audit import THREAT_ASSESSED
from brain.prefrontal_cortex import immutable_rules

router = APIRouter()


class AssessRequest(BaseModel):
    """A request to score one action."""

    category: str = Field(..., description="Action category, e.g. run_safe_commands")
    target: str = Field("", description="Command, path, URL or app name")
    dry_run: bool = Field(True, description="Preview only; never executes")


def action_from(category: str, target: str, dry_run: bool = True) -> Action:
    """Build an Action from request fields.

    Args:
        category: The action category key.
        target: What the action acts on.
        dry_run: Whether this is a preview.

    Returns:
        The action.

    Raises:
        HTTPException: 400 when the category is not one ZUZZ knows.
    """
    try:
        resolved = ActionCategory(category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"unknown action category: {category}") from exc
    return Action(category=resolved, target=target, dry_run=dry_run)


@router.post("/api/safety/assess")
async def assess(request: Request, body: AssessRequest) -> Dict[str, Any]:
    """Score one action for danger, without deciding or executing anything.

    Args:
        request: Used to reach the shared trust manager and audit log.
        body: The action to assess.

    Returns:
        The threat assessment and, for context, the decision it would force.
    """
    action = action_from(body.category, body.target, body.dry_run)
    threat = ThreatDetector().assess(action)
    level = request.app.state.trust.current()
    decision = PermissionGate().decide(action, level, threat)
    request.app.state.audit.record(
        THREAT_ASSESSED,
        threat.explanation,
        category=str(action.category),
        target=action.target,
        level=threat.level,
        trust_level=level,
        score=threat.score,
        matched=threat.matched,
    )
    return {"threat": threat.to_dict(), "decision": decision.to_dict()}


@router.get("/api/safety/audit")
async def audit(request: Request, event: str = "", count: int = 50) -> Dict[str, Any]:
    """Return the safety audit trail.

    Args:
        request: Used to reach the shared audit log.
        event: Optional event type to filter on.
        count: How many entries to return.

    Returns:
        Safety-critical events, separate from the noisy activity log.
    """
    audit_log = request.app.state.audit
    entries = audit_log.by_event(event, count) if event else audit_log.tail(count)
    return {"entries": entries, "path": str(audit_log.path)}


@router.get("/api/safety/rules")
async def rules() -> Dict[str, Any]:
    """Return the immutable rules and their integrity digest.

    Returns:
        The rule list plus the digest, so the UI can show that the rules have
        not been altered.
    """
    return {"rules": RULES, "digest": immutable_rules.digest()}


@router.get("/api/safety/categories")
async def categories() -> List[str]:
    """Return every action category the gate knows about."""
    return [str(category) for category in ActionCategory]