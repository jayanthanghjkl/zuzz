"""Face API — safety routes.

Exposes the Prefrontal Cortex so the UI can see what ZUZZ is allowed to do,
preview an action without performing it, and change the trust level through the
escalating confirmation approved on 2026-10-02.

Threat scoring lives in `routes_assess.py`. Nothing here executes an action:
`/api/safety/check` is a dry run by construction, and the Body layer is not wired
up yet.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action
from brain.prefrontal_cortex.immutable_rules import RULES
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_manager import TrustChangeRefused
from core.config import get_trust_levels
from face.api.routes_assess import AssessRequest, action_from

router = APIRouter()


class TrustChangeRequest(BaseModel):
    """A request to move the trust level, with its confirmation evidence."""

    level: int = Field(..., ge=1, le=5)
    confirmed: bool = Field(False, description="The user clicked confirm")
    typed_word: str = Field("", description="Required for level 5")
    acknowledged_cooldown: bool = Field(False, description="Required for level 5")


@router.get("/api/safety/trust")
async def trust_status(request: Request) -> Dict[str, Any]:
    """Return the current trust level, the matrix and the immutable rules.

    Args:
        request: Used to reach the shared trust manager.

    Returns:
        The full permission picture the UI needs to render controls.
    """
    trust = request.app.state.trust
    config = get_trust_levels()
    return {
        "current": trust.current(),
        "levels": config.get("levels", {}),
        "actions": config.get("actions", {}),
        "policy": config.get("policy", {}),
        "rules": RULES,
        "change": trust.describe_change(5),
    }


@router.post("/api/safety/trust/preview")
async def trust_preview(request: Request, body: TrustChangeRequest) -> Dict[str, Any]:
    """Return what a trust level change would require, without applying it.

    Args:
        request: Used to reach the shared trust manager.
        body: The level being considered.

    Returns:
        Current level, target level and the confirmation requirement.
    """
    return request.app.state.trust.describe_change(body.level)


@router.post("/api/safety/trust")
async def trust_change(request: Request, body: TrustChangeRequest) -> Dict[str, Any]:
    """Change the trust level once its confirmation requirement is met.

    Args:
        request: Used to reach the shared trust manager.
        body: The level and the confirmation evidence supplied by the user.

    Returns:
        The new trust level.

    Raises:
        HTTPException: 403 when the confirmation requirement is not met.
    """
    trust = request.app.state.trust
    try:
        level = trust.request_change(
            body.level,
            confirmed=body.confirmed,
            typed_word=body.typed_word,
            acknowledged_cooldown=body.acknowledged_cooldown,
        )
    except TrustChangeRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    request.app.state.logger.log(
        "prefrontal_cortex",
        "trust_change",
        level=level,
        previous=body.level,
        source="ui",
    )
    request.app.state.audit.record(
        "trust_change",
        f"trust level set to {level}",
        trust_level=level,
        source="ui",
    )
    return {"trust_level": level, **trust.describe_change(level)}


@router.post("/api/safety/check")
async def check(request: Request, body: AssessRequest) -> Dict[str, Any]:
    """Preview the permission decision for one action.

    Always a dry run: this endpoint reports, it never acts.

    Args:
        request: Used to reach the shared trust manager.
        body: The action to preview.

    Returns:
        The decision the Body layer would be given, with the threat that led
        to it. While the kill switch is engaged this is always a block.
    """
    action: Action = action_from(body.category, body.target, body.dry_run)
    threat = ThreatDetector().assess(action)
    gate = PermissionGate(kill_switch=request.app.state.kill_switch)
    decision = gate.decide(action, request.app.state.trust.current(), threat)
    payload = decision.to_dict()
    payload["threat"] = threat.to_dict()
    return payload