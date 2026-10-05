"""Face API — system-awareness request models and the shared permission flow.

The six Parietal endpoints share one flow, and it is deliberately the same flow
:mod:`face.api.vision_support` uses rather than a sixth copy of it. A user who has
learned to read one set of confirmation prompts can read the other, and the audit
record looks the same whichever subsystem answered.

1. Build the :class:`~brain.prefrontal_cortex.action.Action` through
   :meth:`~brain.parietal_lobe.parietal_lobe.ParietalLobe.propose`, so the target the
   gate decides about is the exact string the region will later insist on.
2. Have the Amygdala score it.
3. Have the gate decide at the current trust level.
4. ``blocked`` → 403 (409 when the kill switch is engaged). ``ask`` → 202 with a
   ``confirmation_id``. ``auto`` → execute and return 200.

**Every one of these endpoints auto-approves at every trust level**, so in practice
the 202 branch is unreachable and the flow looks like ceremony. It is kept anyway, for
two reasons that are easy to state and hard to design around later: the
``system_awareness`` row is a *configuration*, and a user who edits it to ``ask`` —
which the file explicitly invites them to consider — should not need a second
implementation of the endpoint to match; and the kill switch check at step 0 has to
exist somewhere regardless of what the matrix says.

**``/api/parietal/status`` is not gated.** It reports the region's configuration and
whether the layout can be read, which is the same class of read as the trust level
itself — the panel a user consults to decide whether to grant anything.

Status codes: 200 success, 202 awaiting confirmation, 400 bad input, 403 blocked by
rule or by the region, 409 kill switch, 503 the platform would not answer, 500
internal.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.parietal_lobe.exceptions import (
    DecisionRefused,
    DryRunRefused,
    KillSwitchRefused,
    NoSuchMonitor,
    ParietalRefused,
    SystemAwarenessUnavailable,
)
from brain.parietal_lobe.parietal_guards import SYSTEM_AWARENESS, target_for
from brain.parietal_lobe.parietal_lobe import ParietalLobe
from core.audit import BLOCKED, CONFIRMATION_REQUESTED

from face.api.file_confirmations import confirmation_payload

#: Refusals that are the caller's input being wrong, not the system's.
BAD_INPUT = (NoSuchMonitor, ValueError, TypeError, KeyError)


class ParietalRequest(BaseModel):
    """Fields every system-awareness request accepts."""

    monitor: int = Field(0, description="Zero-based monitor index")
    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")


class RunningAppsRequest(ParietalRequest):
    """A running-applications request."""

    include_hidden: bool = Field(
        False, description="Also list processes with no visible window"
    )
    limit: int = Field(50, description="Maximum applications to return")


def lobe(request: Request) -> ParietalLobe:
    """Return the Parietal Lobe wired into the app.

    Args:
        request: The incoming request.

    Returns:
        The region.

    Raises:
        HTTPException: 503 when the region was never wired. A server with no parietal
            region cannot serve system awareness, and saying so plainly beats an
            AttributeError from deep inside a route.
    """
    region = getattr(request.app.state, "parietal_lobe", None)
    if region is None:
        raise HTTPException(
            status_code=503, detail="the system awareness region is not available"
        )
    return region


def gate(request: Request, action: Any):
    """Score and decide one system-awareness action.

    Args:
        request: Used to reach the app's gate and trust level.
        action: The proposed action, built by the region's ``propose``.

    Returns:
        The Amygdala's assessment and the gate's verdict.
    """
    level = request.app.state.trust.current()
    threat = ThreatDetector().assess(action)
    decision = request.app.state.permission_gate.decide(action, level, threat)
    return threat, decision


def audit(request: Request, event: str, target: str, decision: Any) -> None:
    """Record a safety event in the audit log.

    Args:
        request: Used to reach the audit log.
        event: The event type.
        target: What the action acted on. Never a window title — see
            :mod:`brain.parietal_lobe.parietal_guards`.
        decision: The verdict, used for its reason and level.

    Returns:
        Nothing.
    """
    log = getattr(request.app.state, "audit", None)
    if log is None:
        return
    log.record(
        event,
        decision.reason,
        category=SYSTEM_AWARENESS,
        target=target,
        level=decision.decision,
        rule_id=decision.rule_id or "",
        trust_level=decision.trust_level,
    )


async def run(
    request: Request,
    operation: str,
    execute: Callable[[Any], Any],
    body: ParietalRequest,
    preview: Callable[[], Dict[str, Any]],
    monitor: Optional[int] = None,
) -> Dict[str, Any]:
    """Gate, confirm and run one system read.

    Args:
        request: Used to reach the app's collaborators.
        operation: The region's operation name, for the target string.
        execute: The region call, invoked only with an allowed decision. May be
            synchronous or a coroutine function; the region's six methods are all
            synchronous because they read the operating system and there is nothing
            to await, but a wrapper that needs to be async must still work.
        body: The request, including confirmation evidence.
        preview: What the confirmation popup should show.
        monitor: Zero-based monitor index, for the operations that take one.

    Returns:
        The result, a dry-run plan, or a confirmation prompt.

    Raises:
        HTTPException: Mapped from the region's refusals.
    """
    region = lobe(request)
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    action = region.propose(operation, monitor)
    threat, decision = gate(request, action)
    if decision.blocked:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if decision.requires_confirmation and not body.confirmed:
        confirmation_id = request.app.state.confirmations.create(
            action, decision, preview()
        )
        audit(request, CONFIRMATION_REQUESTED, action.target, decision)
        return JSONResponse(
            status_code=202,
            content={
                "status": "confirmation_required",
                **confirmation_payload(
                    confirmation_id, operation, decision, threat, preview()
                ),
            },
        )
    if decision.requires_confirmation:
        decision = confirm(request, body, action)
    try:
        result = execute(decision)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        audit(request, BLOCKED, action.target, _refuse_as(exc, decision))
        raise refuse(exc) from exc
    request.app.state.logger.log(
        "face", "parietal_api_ok", operation=operation, target=action.target
    )
    return {"status": "ok", "result": result, "decision": decision.to_dict()}


def confirm(request: Request, body: ParietalRequest, action: Any):
    """Answer a parked confirmation.

    The parked entry is **popped**, not read, so a confirmation is single-use. The
    action is re-decided here rather than upgraded blindly, so a stop that landed
    while the popup was open still blocks, and the parked action must be the action
    being requested — which for this region is the thing that matters most, since an
    approval for "which window am I in" must not be spendable on "list every process".

    Args:
        request: Used to reach the app's collaborators.
        body: The request carrying the confirmation evidence.
        action: The action being requested now.

    Returns:
        An allowed decision.

    Raises:
        HTTPException: 403 when the id is unknown, already spent, or for another
            action; 409 when the stop is engaged; 403 when the re-decision no longer
            permits the action.
    """
    if not body.confirmation_id:
        raise HTTPException(status_code=403, detail="this action still needs confirmation")
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    parked = request.app.state.confirmations.pop(body.confirmation_id)
    if parked is None or parked["action"].target != action.target:
        raise HTTPException(
            status_code=403,
            detail="that confirmation has expired or was for a different action",
        )
    threat, decision = gate(request, action)
    if not decision.allowed:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(
            status_code=409 if decision.blocked else 403, detail=decision.reason
        )
    return decision


def preview(operation: str, monitor: Optional[int] = None) -> Dict[str, Any]:
    """Return what a system-awareness confirmation popup shows.

    Args:
        operation: The region's operation name.
        monitor: The requested monitor, when the operation takes one.

    Returns:
        The operation, the target, and two explicit statements about what this read
        does *not* do. The popup names the operation and never the window title: a
        confirmation that displayed the title would disclose the very thing the user
        is being asked about, before they agreed to it.
    """
    return {
        "operation": operation,
        "monitor": monitor,
        "target": target_for(operation),
        "reads_screen": False,
        "reads_window_title": operation
        in ("active-window", "detect-context", "snapshot"),
        "writes_disk": False,
        "mutates_anything": False,
    }


def refuse(exc: Exception) -> HTTPException:
    """Map a system-awareness refusal onto an HTTP status.

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise. A platform that will not answer is a 503 rather
        than a 500: the system is healthy and something it needs is not there, which
        is a different thing from a defect and is worth telling apart in a log.
    """
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, (DecisionRefused, DryRunRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, KillSwitchRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SystemAwarenessUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, BAD_INPUT):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ParietalRefused):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


def _refuse_as(exc: Exception, decision: Any):
    """Return a decision carrying the refusal's reason, for the audit record.

    Args:
        exc: The refusal.
        decision: The verdict that permitted the call.

    Returns:
        A stand-in carrying the decision's fields with the refusal's message, so the
        audit entry shows *why* the call failed rather than the permission reason that
        let it start.
    """
    from dataclasses import replace

    return replace(decision, reason=str(exc))
