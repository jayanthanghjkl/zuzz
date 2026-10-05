"""Face API — vision request models and the shared permission flow.

The five vision endpoints share one flow, and it is the flow the file and desktop
routes already use, deliberately: a user who has learned to read one set of
confirmation prompts can read the other.

1. Build the :class:`~brain.prefrontal_cortex.action.Action` through
   :meth:`~brain.occipital_lobe.occipital_lobe.OccipitalLobe.propose`, so the target
   the gate decides about is the exact string the region will later insist on. A
   decision built from a hand-written target would be refused by the region's own
   guard — which is the right outcome, but a confusing one to debug.
2. Have the Amygdala score it.
3. Have the gate decide at the current trust level.
4. ``blocked`` → 403 (409 when the kill switch is engaged). ``ask`` → 202 with a
   ``confirmation_id``. ``auto`` → execute and return 200.

A confirmation is answered by re-posting the same request with ``confirmed: true``
and the ``confirmation_id``. The parked action is re-decided at that moment, so a
stop that landed while the popup was open still blocks.

Status codes: 200 success, 202 awaiting confirmation, 400 bad request or a refused
crop, 403 blocked by rule or classification, 404 no such screenshot, 409 kill switch
or no VRAM for the model, 503 the model could not be reached, 500 internal.

Kept separate from the route module so the route file holds endpoints and this file
holds the flow, which is the same split :mod:`face.api.file_confirmations` and
:mod:`face.api.resource_confirmations` make.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.occipital_lobe.exceptions import (
    CropOutOfBounds,
    DecisionRefused,
    DryRunRefused,
    ElementNotFound,
    KillSwitchRefused,
    MonitorNotFound,
    OcrUnavailable,
    SensitiveScreenDetected,
    VisionModelUnavailable,
    VisionTimeout,
)
from brain.occipital_lobe.occipital_lobe import OccipitalLobe
from brain.occipital_lobe.vision_guards import VISUAL_CAPTURE, screenshot_target_for, target_for
from core.audit import BLOCKED, CONFIRMATION_REQUESTED

from face.api.file_confirmations import confirmation_payload

#: Refusals that are the caller's input being wrong, not the system's.
BAD_INPUT = (CropOutOfBounds, MonitorNotFound, ValueError, TypeError, KeyError)


class VisionRequest(BaseModel):
    """Fields every vision request accepts."""

    monitor: int = Field(0, description="Zero-based monitor index")
    region: Optional[list] = Field(
        None, description="(x, y, width, height) in monitor-local pixels"
    )
    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")


class CaptureRequest(VisionRequest):
    """A capture, with storage options."""

    store: bool = Field(False, description="Write the screenshot to storage")
    label: str = Field("", description="Short name for the stored file")
    acknowledge_sensitive: bool = Field(
        False, description="Store a screen carrying credentials anyway"
    )
    consult_model: bool = Field(
        False, description="Also ask the vision model about credential surfaces"
    )


class DescribeRequest(VisionRequest):
    """A description request."""

    question: str = Field("", description="Optional focus, e.g. what this dialog asks")


class FindRequest(VisionRequest):
    """An element-location request."""

    description: str = Field(..., description="What to look for, in the user's words")


class VerifyRequest(VisionRequest):
    """A before/after comparison."""

    before: str = Field(..., description="Stored screenshot name, or 'current'")
    after: str = Field(..., description="Stored screenshot name, or 'current'")
    label: str = Field("", description="What the comparison is about")


class ReadRequest(BaseModel):
    """Evidence fields for a gated read that is not a vision operation.

    Used by the screenshot fetch, which is a ``visual_capture`` read of a file
    rather than of a screen, and so needs the same confirmation dance without
    pretending to be a capture.
    """

    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")


def lobe(request: Request) -> OccipitalLobe:
    """Return the Occipital Lobe wired into the app.

    Args:
        request: The incoming request.

    Returns:
        The region.

    Raises:
        HTTPException: 503 when the region was never wired. A server with no vision
            region cannot serve vision, and saying so plainly beats an
            AttributeError from deep inside a route.
    """
    region = getattr(request.app.state, "occipital_lobe", None)
    if region is None:
        raise HTTPException(status_code=503, detail="the vision region is not available")
    return region


def gate(request: Request, action: Any):
    """Score and decide one vision action.

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
        target: What the action acted on.
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
        category=VISUAL_CAPTURE,
        target=target,
        level=decision.decision,
        rule_id=decision.rule_id or "",
        trust_level=decision.trust_level,
    )


async def run(
    request: Request,
    operation: str,
    monitor: int,
    region: Optional[list],
    body: VisionRequest,
    execute: Callable[[Any], Any],
    preview: Callable[[], Dict[str, Any]],
    action: Optional[Any] = None,
) -> Dict[str, Any]:
    """Gate, confirm and run one vision operation.

    Args:
        request: Used to reach the app's collaborators.
        operation: The region's operation name, for the target string.
        monitor: Zero-based monitor index.
        region: ``(x, y, width, height)`` in monitor-local pixels, or None.
        body: The request, including confirmation evidence.
        execute: The region call, invoked only with an allowed decision.
        preview: What the confirmation popup should show.
        action: A pre-built action, for a read that is not one of the five
            operations. Built from the region when None.

    Returns:
        The result, a dry-run plan, or a confirmation prompt.

    Raises:
        HTTPException: Mapped from the region's refusals.
    """
    region_impl = lobe(request)
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    action = action if action is not None else region_impl.propose(operation, monitor, region)
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
                    confirmation_id,
                    operation,
                    decision,
                    threat,
                    preview(),
                ),
            },
        )
    if decision.requires_confirmation:
        decision = confirm(request, body, action)
    try:
        result = await execute(decision)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        audit(request, BLOCKED, action.target, _refuse_as(exc, decision))
        raise refuse(exc) from exc
    request.app.state.logger.log(
        "face", "vision_api_ok", operation=operation, target=action.target
    )
    return {"status": "ok", "result": result, "decision": decision.to_dict()}


def confirm(request: Request, body: VisionRequest, action: Any):
    """Answer a parked confirmation.

    The parked entry is **popped**, not read, so a confirmation is single-use: a
    replay of the same id finds nothing and is refused. The action is re-decided
    here rather than upgraded blindly, so a stop that landed while the popup was
    open still blocks, and the parked action must be the action being requested — an
    approval for "look at monitor 0" cannot be spent on "photograph monitor 1".

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


def preview(operation: str, monitor: int, region: Optional[list]) -> Dict[str, Any]:
    """Return what a vision confirmation popup shows.

    Args:
        operation: The region's operation name.
        monitor: Zero-based monitor index.
        region: The requested crop, or None.

    Returns:
        The operation, the target, and the crop — everything the user needs to see
        what they are approving, and nothing about the screen itself.
    """
    return {
        "operation": operation,
        "monitor": monitor,
        "region": list(region) if region else None,
        "target": target_for(operation, monitor, region),
        "reads_screen": True,
        "writes_disk": operation == "capture",
    }


def read_preview(name: str) -> Dict[str, Any]:
    """Return what a screenshot-fetch confirmation popup shows.

    Args:
        name: The bare file name being fetched.

    Returns:
        The operation, the target, and the name. The *contents* are not described:
        showing them in the popup would hand over the frame before the decision.
    """
    return {
        "operation": "read-screenshot",
        "screenshot": name,
        "target": screenshot_target_for(name),
        "reads_screen": True,
        "writes_disk": False,
    }


def refuse(exc: Exception) -> HTTPException:
    """Map a vision refusal onto an HTTP status.

    The hierarchy does the work: a stop outranks the action, and a crop ZUZZ must
    not silently alter is a 400 whatever the trust level was. A missing model is a
    503 — the system is healthy and something it needs is not there — while a refused
    *element* is a 404, because the screen was read perfectly well and the answer
    genuinely is "there is no such button".

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise.
    """
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, (SensitiveScreenDetected, DecisionRefused, DryRunRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (VisionTimeout, VisionModelUnavailable, OcrUnavailable)):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ElementNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, BAD_INPUT):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, KillSwitchRefused):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


def _refuse_as(exc: Exception, decision: Any):
    """Return a decision carrying the refusal's reason, for the audit record.

    Args:
        exc: The refusal.
        decision: The verdict that permitted the call.

    Returns:
        A stand-in carrying the decision's fields with the refusal's message, using
        :func:`dataclasses.replace` so the audit entry shows *why* the call failed
        rather than the permission reason that let it start.
    """
    from dataclasses import replace

    return replace(decision, reason=str(exc))