"""Face API — habits routes.

The saved routines: listing them, changing them, and planning a run.

**``POST /api/habits/execute`` plans; it does not run.** The response is the plan:
every step with its verdict at the current trust level, which steps need the user
to confirm, and which are forbidden. Two reasons that is the whole design:

* a routine's steps are separate acts with separate verdicts, and they are rarely
  all ``auto`` at the same level. The seeds show it: ``start_work`` is fully
  runnable only at trust level 5, because its third step is ``browser_navigate``
  and ``browse_internet`` asks at levels 1-4. A routine that closes an application
  can never run unattended, because ``close_apps`` asks at all five. Confirming the
  steps is the correct path in both cases;
* executing in order and stopping at the first refusal would leave a five-step
  routine half-applied — two applications open, nothing focused, no single user
  action to explain it. :meth:`~brain.basal_ganglia.basal_ganglia.BasalGanglia.plan`
  checks everything before anything runs.

So a caller that wants to *actually* run a routine calls this, reads the plan, and
issues the permitted steps as ordinary gated tool calls. ``confirm_steps`` is
accepted and echoed so the response can say which pending steps have been dealt
with.

**Arming is its own endpoint.** ``POST /api/habits/arm`` exists so that switching a
routine on is a deliberate act with a route of its own, and so that the response
can carry the plan — what arming just made possible — rather than only a boolean.
``POST /api/habits/routine`` ignores ``enabled`` entirely: a routine that arrives
armed is stored disarmed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.basal_ganglia.exceptions import (
    RoutineDisabled,
    RoutineNameInvalid,
    RoutineNotPermitted,
    UnknownRoutine,
)
from core.audit import ROUTINE_DISABLED, ROUTINE_ENABLED

router = APIRouter()


class RoutineRequest(BaseModel):
    """A request to store a new routine."""

    name: str = Field(..., description="Routine name: lowercase letters, digits, underscores")
    description: str = Field("", description="One line describing what it does")
    steps: List[Dict[str, Any]] = Field(default_factory=list, description="Steps, in order")
    trigger: Dict[str, Any] = Field(
        default_factory=dict, description="When it fires: manual, time, app or command"
    )
    enabled: bool = Field(False, description="Whether it may run unattended")


class RoutinePatchRequest(BaseModel):
    """A request to change a stored routine.

    Every field optional, because the routine is named in the path and a patch
    carries only the fields being changed. ``enabled`` needs an explicit flag
    rather than being inferred from the model's presence: omitting it means "leave
    the routine armed or disarmed as it is", and inferring that from whether a
    default was filled in would make ``{"description": "..."}`` silently disarm an
    armed routine.
    """

    description: Optional[str] = Field(None, description="A new one-line description")
    steps: Optional[List[Dict[str, Any]]] = Field(None, description="New steps, in order")
    trigger: Optional[Dict[str, Any]] = Field(None, description="A new trigger")
    enabled: Optional[bool] = Field(None, description="Whether it may run unattended")


class ExecuteRequest(BaseModel):
    """A request to plan a routine's execution."""

    name: str = Field(..., description="The routine to plan")
    confirm_steps: List[int] = Field(
        default_factory=list, description="Step numbers the user has approved, from 1"
    )


class ArmRequest(BaseModel):
    """A request to arm or disarm a stored routine.

    ``enabled`` is explicit and defaults to ``True`` because this endpoint exists
    only to be the *arming* one. A body that left it out is a request to arm, and a
    caller that meant to disarm has to say so — the failure this guards against is
    a habit page's toggle arriving with the wrong default and quietly re-arming a
    routine the user had just turned off.
    """

    name: str = Field(..., description="The routine to arm or disarm")
    enabled: bool = Field(True, description="True arms it, false disarms it")


def _ganglia(request: Request):
    """Return the Basal Ganglia wired into the app."""
    ganglia = getattr(request.app.state, "basal_ganglia", None)
    if ganglia is None:
        raise HTTPException(status_code=503, detail="routines are not attached to this app")
    return ganglia


def _refused(exc: Exception, tool: str) -> HTTPException:
    """Turn a routine refusal into the right status.

    Args:
        exc: The refusal.
        tool: The operation, for the detail message.

    Returns:
        404 for a routine that is not stored, 409 for one that is switched off, 403
        for a permission refusal, 400 for everything else. They are kept apart
        because they need different answers from a user: a missing routine needs a
        different action from a disabled one, and neither is a malformed request.
    """
    if isinstance(exc, UnknownRoutine):
        return HTTPException(status_code=404, detail=f"{tool}: {exc}")
    if isinstance(exc, RoutineDisabled):
        return HTTPException(status_code=409, detail=f"{tool}: {exc}")
    if isinstance(exc, RoutineNotPermitted):
        return HTTPException(
            status_code=403,
            detail={
                "error": "not_permitted",
                "reason": str(exc),
                "trust_level": exc.trust_level,
                "refused": exc.refused,
                "note": (
                    "every step is checked before any of them runs, so nothing "
                    "was changed"
                ),
            },
        )
    if isinstance(exc, RoutineNameInvalid):
        return HTTPException(status_code=400, detail=f"{tool}: {exc}")
    return HTTPException(status_code=400, detail=f"{tool}: {exc}")


@router.get("/api/habits/routines")
async def list_routines(request: Request, include_steps: bool = False) -> Dict[str, Any]:
    """List the saved routines.

    Args:
        request: Used to reach the Basal Ganglia and the activity log.
        include_steps: Include each routine's steps in full.

    Returns:
        Every routine with its trigger description and step count, and which of
        them are due right now.
    """
    ganglia = _ganglia(request)
    routines = ganglia.list_routines()
    payload = []
    for routine in routines:
        entry = routine.to_dict()
        if not include_steps:
            entry.pop("steps", None)
        payload.append(entry)
    return {
        "count": len(payload),
        "enabled": sum(1 for routine in routines if routine.enabled),
        "routines": payload,
        "due_now": [routine.name for routine in ganglia.due_routines({})],
        "path": str(ganglia.registry.path),
    }


@router.post("/api/habits/routine")
async def create_routine(request: Request, body: RoutineRequest) -> Dict[str, Any]:
    """Save a new routine.

    Stored **disabled** regardless of what the request asked for — ``enabled`` is
    accepted on the body and then ignored, so a client that sets it gets the same
    answer as one that does not. Creating a routine and arming it are two different
    acts, and a POST that did both would mean the user could not look at what they
    had just built before it starts opening applications on a trigger. Arm it with
    ``POST /api/habits/arm``, which is a separate call on purpose.

    Args:
        request: Used to reach the Basal Ganglia, the activity log and the audit
            trail.
        body: The routine to store.

    Returns:
        The routine as stored.

    Raises:
        HTTPException: 400 for an unusable name, an empty step list or a step
            naming something ZUZZ cannot call.
    """
    ganglia = _ganglia(request)
    if not body.steps:
        raise HTTPException(status_code=400, detail="a routine needs at least one step")
    try:
        routine = ganglia.create_routine(
            {
                "name": body.name,
                "description": body.description,
                "steps": body.steps,
                "trigger": body.trigger or {"kind": "manual"},
                "enabled": False,
                "source": "user",
            }
        )
    except Exception as exc:
        raise _refused(exc, "create") from exc

    request.app.state.audit.record(
        "routine_created",
        f"created the routine {routine.name!r} with {len(routine.steps)} step(s)",
        name=routine.name,
        steps=len(routine.steps),
        tools=[step.tool for step in routine.steps],
    )
    request.app.state.logger.log(
        "basal_ganglia", "create", name=routine.name, steps=len(routine.steps)
    )
    return routine.to_dict()


@router.post("/api/habits/arm")
async def arm_routine(request: Request, body: ArmRequest) -> Dict[str, Any]:
    """Arm or disarm a stored routine.

    The one endpoint whose only job is the switch. It exists separately from
    ``PATCH /api/habits/routine/{name}`` — which can also set ``enabled`` — because
    arming is the act with consequences and deserves to be a thing the user does on
    purpose rather than a field that rides along with a description edit.

    **Arming is not permission.** It makes a routine eligible for its trigger. Every
    step is still decided against the trust matrix at the moment the routine runs,
    so a routine armed at level 5 does nothing at level 1 that level 1 would have
    refused, and ``GET /api/habits/execute`` reports exactly what it would do before
    anything happens.

    Args:
        request: Used to reach the Basal Ganglia, the activity log and the audit
            trail.
        body: Which routine, and which way.

    Returns:
        The routine as stored, plus the plan, so the caller can see what arming
        just made possible.

    Raises:
        HTTPException: 404 when the routine is not stored, 400 when the name is
            unusable.
    """
    ganglia = _ganglia(request)
    try:
        routine = ganglia.update_routine(body.name, {"enabled": bool(body.enabled)})
    except Exception as exc:
        raise _refused(exc, "arm" if body.enabled else "disarm") from exc

    request.app.state.audit.record(
        ROUTINE_ENABLED if body.enabled else ROUTINE_DISABLED,
        f"{'armed' if body.enabled else 'disarmed'} the routine {routine.name!r}",
        name=routine.name,
        enabled=routine.enabled,
    )
    request.app.state.logger.log(
        "basal_ganglia",
        "arm" if body.enabled else "disarm",
        name=routine.name,
        enabled=routine.enabled,
    )
    # The plan is the point of answering an arming request: "what can now happen
    # that could not before". It can only be built for an *armed* routine —
    # ``plan`` refuses a disarmed one — so the disarm branch reports that plainly
    # rather than pretending there is nothing to show.
    if body.enabled:
        try:
            plan: Optional[Dict[str, Any]] = ganglia.plan(routine.name).to_dict()
        except Exception as exc:
            request.app.state.logger.log(
                "basal_ganglia", "arm_plan_refused", name=routine.name, reason=str(exc)
            )
            plan = {"error": str(exc)}
    else:
        plan = None

    return {
        **routine.to_dict(),
        "plan": plan,
        "note": (
            "armed; its steps are still decided one by one when it runs"
            if body.enabled
            else "disarmed; it will not fire on its trigger"
        ),
    }


@router.patch("/api/habits/routine/{name}")
async def update_routine(request: Request, name: str, body: RoutinePatchRequest) -> Dict[str, Any]:
    """Change a stored routine.

    Only the fields supplied are applied, so ``{"enabled": true}`` arms a routine
    without touching its steps and ``{"description": "..."}`` does not disarm it.

    Args:
        request: Used to reach the Basal Ganglia and the activity log.
        name: The routine to change.
        body: The fields to change.

    Returns:
        The routine as stored.

    Raises:
        HTTPException: 404 when there is no such routine, 400 when nothing was
            supplied to change.
    """
    ganglia = _ganglia(request)
    changes: Dict[str, Any] = {}
    if body.description is not None:
        changes["description"] = body.description
    if body.steps:
        changes["steps"] = body.steps
    if body.trigger is not None:
        changes["trigger"] = body.trigger
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)
    if not changes:
        raise HTTPException(status_code=400, detail="nothing to change was supplied")

    was_enabled: Optional[bool] = None
    try:
        existing = ganglia.get_routine(name)
        was_enabled = existing.enabled
        routine = ganglia.update_routine(name, changes)
    except Exception as exc:
        raise _refused(exc, "update") from exc

    # Arming a routine is the significant half of this endpoint: it is the point
    # at which stored steps begin to be run on a trigger, so it is the part worth
    # an audit record even though it changes nothing on the machine by itself.
    if was_enabled is not None and routine.enabled and not was_enabled:
        request.app.state.audit.record(
            "routine_enabled",
            f"armed the routine {routine.name!r}",
            name=routine.name,
            steps=len(routine.steps),
            trigger=routine.trigger.to_dict()["description"],
        )
    request.app.state.logger.log(
        "basal_ganglia", "update", name=routine.name, fields=sorted(changes)
    )
    return routine.to_dict()


@router.post("/api/habits/execute")
async def execute(request: Request, body: ExecuteRequest) -> Dict[str, Any]:
    """Plan what a routine would do at the current trust level.

    Runs nothing. Every step is resolved and decided first, so the response is the
    whole picture: what is permitted, what needs confirming, what is forbidden.

    Args:
        request: Used to reach the Basal Ganglia and the activity log.
        body: The routine and any steps the user has already confirmed.

    Returns:
        The plan, with ``executed: false`` and a ``next`` line saying what to do.

    Raises:
        HTTPException: 404 when there is no such routine, 409 when it is switched
            off, 400 when a step cannot be resolved.
    """
    ganglia = _ganglia(request)
    try:
        plan = ganglia.plan(body.name)
    except Exception as exc:
        request.app.state.logger.log(
            "basal_ganglia", "plan_refused", name=body.name, reason=str(exc)
        )
        raise _refused(exc, "execute") from exc

    payload = plan.to_dict()
    payload["confirmed"] = list(body.confirm_steps)
    payload["executed"] = False
    payload["next"] = (
        "run each step as its own tool call, in order"
        if plan.runnable
        else "ask the user to confirm the pending steps first"
        if plan.permitted
        else "this routine cannot run at the current trust level"
    )
    request.app.state.logger.log(
        "basal_ganglia",
        "plan",
        name=plan.routine.name,
        trust_level=plan.trust_level,
        permitted=plan.permitted,
        pending=len(plan.pending),
    )
    return payload


@router.get("/api/habits/routine/{name}")
async def describe_routine(request: Request, name: str) -> Dict[str, Any]:
    """Return one routine together with the plan for running it.

    Args:
        request: Used to reach the Basal Ganglia.
        name: The routine to describe.

    Returns:
        The routine and, when it can be planned, its plan.

    Raises:
        HTTPException: 404 when there is no such routine, 409 when it is disabled.
    """
    ganglia = _ganglia(request)
    try:
        return ganglia.describe_routine(name)
    except Exception as exc:
        raise _refused(exc, "describe") from exc


@router.delete("/api/habits/routine/{name}")
async def delete_routine(request: Request, name: str) -> Dict[str, Any]:
    """Delete a routine.

    Args:
        request: Used to reach the Basal Ganglia, the activity log and the audit
            trail.
        name: The routine to delete.

    Returns:
        Whether it was there and is now gone. ``404`` when it was not, because a
        delete of something that does not exist should be visible rather than
        reported as a success.
    """
    ganglia = _ganglia(request)
    try:
        existing = ganglia.get_routine(name)
    except UnknownRoutine as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    deleted = ganglia.delete_routine(existing.name)
    request.app.state.audit.record(
        "routine_deleted",
        f"deleted the routine {existing.name!r}",
        name=existing.name,
        steps=len(existing.steps),
    )
    request.app.state.logger.log(
        "basal_ganglia", "delete", name=existing.name, deleted=deleted
    )
    return {"name": existing.name, "deleted": bool(deleted)}


@router.get("/api/habits/patterns")
async def patterns(request: Request, min_repetitions: int = 5) -> Dict[str, Any]:
    """Find the habits in the activity log.

    Args:
        request: Used to reach the Basal Ganglia and the activity log.
        min_repetitions: How many times a sequence must appear to count.

    Returns:
        The detected patterns, strongest first. Read-only — building a routine from
        one is a separate, gated act.
    """
    from brain.basal_ganglia.pattern_detector import MAX_WINDOW, DEFAULT_MIN_REPETITIONS

    repetitions = max(2, min(int(min_repetitions), 100))
    ganglia = _ganglia(request)
    found = ganglia.detect_patterns(min_repetitions=repetitions)
    return {
        "count": len(found),
        "min_repetitions": repetitions,
        "default_min_repetitions": DEFAULT_MIN_REPETITIONS,
        "max_window": MAX_WINDOW,
        "patterns": [pattern.to_dict() for pattern in found],
    }