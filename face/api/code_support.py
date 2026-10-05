"""Face API — support for the coding routes.

The gate/confirm/run cycle for :mod:`face.api.routes_code`, in the shape
:mod:`face.api.browser_support` uses. Split out so the route module stays a thin
list of endpoints, and so this module's only two additions to the browser's version
are visible on their own.

**Two decisions where the browser needs one.** Applying a change set is a
``modify_project`` act *and* a ``write_files`` act — the first says "this project may
be rewritten", the second names the root the bytes land in. One 202 answers both and
``write_action`` in the payload says what the second one is about, because a popup
showing only a proposal id would not tell the user that files are about to be
overwritten. That is the download's pattern, for the download's reason.

**A workspace confirmation.** A folder outside the scratch root and outside every
registered one is not refused — it is a question, and it becomes the same 202 every
other confirmation becomes, carrying the folder. ZUZZ may work in a project the user
has not registered; it may not do so until the user has said the folder is fine. This
is :class:`~body.mouth.kilo.exceptions.WorkspaceConfirmationRequired` arriving, and
this is the only place in the codebase that answers it — which is what keeps the
refusal honest, since a caller that cannot answer it gets a 403 instead.

The gauges are deliberately *not* gated: :meth:`body.mouth.kilo.kilo_engine.KiloEngine.status`,
:meth:`~body.mouth.kilo.kilo_engine.KiloEngine.preview_diff`,
:meth:`~body.mouth.kilo.kilo_engine.KiloEngine.workspace_tree` and
:meth:`~body.mouth.kilo.kilo_engine.KiloEngine.git_status` report ZUZZ's own state and
are what a user reads *in order to decide*. A gauge behind a confirmation is a gauge
nobody looked at.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.mouth.kilo.exceptions import (
    AgentRefused,
    CommandRefused,
    CommandTimedOut,
    DiffAlreadyApplied,
    DiffNotFound,
    DiffRefused,
    GitRefused,
    InstallRefused,
    KiloRefused,
    KillSwitchRefused,
    ModelUnavailable,
    PackageNameInvalid,
    WorkspaceConfirmationRequired,
    WorkspaceNotFound,
    WorkspaceRefused,
)

from core.audit import BLOCKED, CONFIRMATION_REQUESTED, PRECHECK_REFUSAL
from face.api.file_confirmations import confirmation_payload

#: Refusals that are the caller's input being wrong, not the system's. Checked
#: *before* :data:`FORBIDDEN`, because the two hierarchies overlap and the more
#: specific one has to win: ``PackageNameInvalid`` is an ``InstallRefused``, and a
#: name that is not a package is a 400 rather than a 403 the user cannot act on.
BAD_INPUT = (
    PackageNameInvalid,
    AgentRefused,
    DiffRefused,
    CommandRefused,
    ValueError,
    TypeError,
    KeyError,
)

#: Refusals that mean the thing asked about is not there.
NOT_FOUND = (DiffNotFound, WorkspaceNotFound)

#: Refusals that mean state changed underfoot: the file moved since the preview, the
#: diff was already applied, the stop landed, the command ran out of time.
CONFLICT = (DiffAlreadyApplied, CommandTimedOut, KillSwitchRefused)

#: Refusals that are ZUZZ's own reading of the world, and that raising the trust
#: level would not change.
FORBIDDEN = (WorkspaceRefused, GitRefused, InstallRefused)


class CodeRequest(BaseModel):
    """Fields every coding request accepts.

    Attributes:
        workspace: A workspace name or absolute path. Never inside ZUZZ's tree.
        confirmed: The user answered the 202 prompt.
        confirmation_id: The id returned with that 202. Single-use.
        dry_run: Describe the action rather than perform it.
    """

    workspace: str = Field("", description="Workspace name or path; blank is the scratch one")
    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")
    dry_run: bool = Field(False, description="Describe the action, do not perform it")


def engine(request: Request):
    """Return the Kilo engine wired into the app.

    Args:
        request: The incoming request.

    Returns:
        The :class:`~body.mouth.kilo.kilo_engine.KiloEngine`.

    Raises:
        HTTPException: 503 when the engine is not attached. Serving a Code page
            whose every button refuses would be worse than saying so.
    """
    kilo = getattr(request.app.state, "kilo", None)
    if kilo is None:
        raise HTTPException(
            status_code=503, detail="the coding agents are not attached to this app"
        )
    return kilo


def gate(request: Request, action: Action):
    """Return the Amygdala's assessment and the gate's verdict for an action.

    Args:
        request: Used to reach the trust manager, the gate and the Amygdala.
        action: The proposed action.

    Returns:
        The threat assessment and the decision.
    """
    level = request.app.state.trust.current()
    threat = ThreatDetector().assess(action)
    decision = request.app.state.permission_gate.decide(action, level, threat)
    return threat, decision


def audit(
    request: Request,
    event: str,
    target: str,
    decision: Any = None,
    **fields: Any,
) -> None:
    """Record a safety event in the audit log.

    Args:
        request: Used to reach the audit log.
        event: The event type.
        target: What the action acted on.
        decision: The verdict, used for its reason and level.
        **fields: Extra detail recorded verbatim.

    Returns:
        Nothing. An audit log that is not wired loses the event rather than failing
        the request: the decision is already made and the caller still needs an
        answer.
    """
    log = getattr(request.app.state, "audit", None)
    if log is None:
        return
    reason = getattr(decision, "reason", "") or ""
    log.record(
        event,
        reason,
        category=str(decision.action.category) if decision else "",
        target=target,
        level=getattr(decision, "decision", "") or "",
        rule_id=getattr(decision, "rule_id", "") or "",
        trust_level=getattr(decision, "trust_level", None),
        **fields,
    )


def refuse(exc: Exception) -> HTTPException:
    """Map a Kilo refusal onto the status it deserves.

    Args:
        exc: The refusal.

    Returns:
        The exception to raise. 400 for input the caller got wrong, 403 for the gate
        and ZUZZ's own refusals, 404 for a proposal or workspace that is not there,
        409 for state that changed underfoot, 429 for the model provider. They are
        kept apart because they need different answers from a user: a missing
        proposal needs a different action from a refused one, and neither is a
        malformed request.
    """
    message = str(exc)
    if isinstance(exc, BAD_INPUT):
        return HTTPException(status_code=400, detail=message)
    if isinstance(exc, NOT_FOUND):
        return HTTPException(status_code=404, detail=message)
    if isinstance(exc, CONFLICT):
        return HTTPException(status_code=409, detail=message)
    if isinstance(exc, ModelUnavailable):
        return HTTPException(status_code=429, detail=message)
    if isinstance(exc, (FORBIDDEN, KiloRefused)):
        return HTTPException(status_code=403, detail=message)
    return HTTPException(status_code=400, detail=message)


async def confirm(request: Request, body: CodeRequest, action: Action) -> PermissionDecision:
    """Answer a parked confirmation.

    The parked entry is **popped**, so a confirmation is single-use: a replay of the
    same id finds nothing and is refused. The action is re-decided here rather than
    upgraded blindly, so a stop that landed while the popup was open still blocks,
    and an approval for one workspace cannot be spent on another — the parked target
    has to equal the requested one, byte for byte.

    Args:
        request: Used to reach the app's collaborators.
        body: The request carrying the confirmation evidence.
        action: The action being requested now.

    Returns:
        An allowed decision.

    Raises:
        HTTPException: 403 when the id is unknown, spent, or for another action;
            409 while the stop is engaged; 403 when the re-decision blocks.
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
    _threat, decision = gate(request, action)
    if decision.blocked:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if not decision.allowed:
        # The user's answer is what turns an ``ask`` into an ``allow``, and the Body
        # needs an ``allow`` because it only ever acts on one. Without this a level-1
        # assistant could never be driven at all: every confirmation would re-ask
        # and the user would sit at a prompt that never closes. ``blocked`` is
        # different — it means no trust level would permit this, so it refuses.
        decision = replace(
            decision, allowed=True, requires_confirmation=False, decision="allow",
            reason=f"confirmed by the user; the gate had said: {decision.reason}",
        )
    return decision


def prompt(
    request: Request,
    action: Action,
    decision: Any,
    threat: Any,
    contents: Dict[str, Any],
    operation: str,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Park an action and return the 202 prompt.

    Args:
        request: Used to park the action and reach the audit log.
        action: The proposed action.
        decision: The ``ask`` verdict.
        threat: The Amygdala's assessment.
        contents: What the popup shows. ``file_count`` and ``total_bytes`` are the
            keys :func:`face.api.file_confirmations.confirmation_payload` reads.
        operation: The operation name.
        extra: Extra fields merged into the payload — ``write_action`` for the two
            decisions, ``workspace`` for an unapproved folder.

    Returns:
        The 202 response.
    """
    confirmation_id = request.app.state.confirmations.create(action, decision, contents)
    audit(request, CONFIRMATION_REQUESTED, action.target, decision, operation=operation)
    return JSONResponse(
        status_code=202,
        content={
            "status": "confirmation_required",
            **confirmation_payload(confirmation_id, operation, decision, threat, contents),
            "action": {
                "category": str(action.category),
                "target": action.target,
                "operation": action.detail.get("operation", operation),
                "dry_run": action.dry_run,
            },
            **(extra or {}),
        },
    )


async def workspace_prompt(
    request: Request,
    action: Action,
    decision: Any,
    threat: Any,
    contents: Dict[str, Any],
    operation: str,
    exc: WorkspaceConfirmationRequired,
) -> JSONResponse:
    """Turn an unknown-but-plausible project folder into the 202 prompt.

    Split out of :func:`run` because it is the one answer in this flow that is not
    about the gate, and because it needs the folder's path in the payload so the popup
    can name the folder rather than showing nothing.

    Args:
        request: Used to park the action.
        action: The action that was refused.
        decision: The verdict, downgraded to an ask.
        threat: The Amygdala's assessment.
        contents: The popup contents.
        operation: The operation name.
        exc: The refusal, which carries the folder.

    Returns:
        The 202, asking about the folder.
    """
    downgraded = replace(
        decision, decision="ask", requires_confirmation=True, allowed=False,
        reason=f"{decision.reason}; {exc}",
    )
    return prompt(
        request, action, downgraded, threat, contents, operation,
        {
            "workspace_confirmation": exc.workspace,
            "needs_workspace_confirmation": True,
        },
    )


async def run(
    request: Request,
    operation: str,
    body: CodeRequest,
    execute: Callable[[PermissionDecision], Any],
    contents: Callable[[], Dict[str, Any]],
    action: Optional[Action] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate, confirm and run one coding operation.

    Args:
        request: Used to reach the app's collaborators.
        operation: One of :data:`body.mouth.kilo.kilo_engine.CATEGORIES`.
        body: The request, including confirmation evidence.
        execute: The engine call, invoked only with an allowed decision.
        contents: What the confirmation popup should show.
        action: A pre-built action, for the operations that need the target built
            rather than guessed — apply, rollback, install.
        extra: Extra fields merged into a 202 payload.

    Returns:
        The result, a dry-run plan, or a 202 confirmation prompt.

    Raises:
        HTTPException: Mapped from the Kilo layer's refusals.
    """
    stack = engine(request)
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    action = action if action is not None else stack.propose(operation)
    threat, decision = gate(request, action)
    if decision.blocked:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if body.dry_run and not action.dry_run:
        # A dry run is a description, not a lighter action, so it is turned into a
        # ``dry_run`` action *before* the gate scores it — a dry run can then never be
        # reported as the performance of the real thing.
        action = replace(action, dry_run=True)
        threat, decision = gate(request, action)
    if decision.requires_confirmation and not body.confirmed:
        return prompt(request, action, decision, threat, contents(), operation, extra)
    if decision.requires_confirmation:
        decision = await confirm(request, body, action)
    if action.dry_run:
        # Described, not performed. Short-circuited before ``execute`` because the
        # Body refuses a dry-run decision outright — correct for a direct caller, and
        # the wrong answer for someone who ticked "dry run" and is owed a reply.
        # Nothing is written to the audit log: a dry run changed nothing.
        return {
            "status": "dry_run",
            "plan": {**contents(), "decision": decision.to_dict(),
                     "threat_score": threat.score},
            "decision": decision.to_dict(),
        }
    try:
        result = await execute(decision)
    except WorkspaceConfirmationRequired as exc:
        return await workspace_prompt(
            request, action, decision, threat, contents(), operation, exc
        )
    except Exception as exc:  # noqa: BLE001 — every refusal is mapped, not leaked
        if isinstance(exc, HTTPException):
            raise
        audit(request, PRECHECK_REFUSAL, action.target, decision,
              operation=operation, error=str(exc))
        raise refuse(exc) from exc
    request.app.state.logger.log(
        "face", "code_api_ok", operation=operation, target=action.target,
        dry_run=bool(action.dry_run),
    )
    return {"status": "ok", "result": result, "decision": decision.to_dict()}


def write_action(root: str, operation: str = "apply") -> Action:
    """Build the second decision an apply or a rollback needs.

    Applying a change set is two acts at two levels: ``modify_project`` says this
    project may be rewritten, and ``write_files`` names the root the bytes land in.
    Finger treats a directory decision as covering everything beneath it, so one
    ``write_files`` decision on the workspace root carries the whole change set.

    Args:
        root: The workspace root.
        operation: The operation the decision is for, for the log.

    Returns:
        The action, for the gate to decide alongside the primary one.
    """
    return Action(
        category=ActionCategory.WRITE_FILES, target=str(root),
        detail={"operation": operation, "reason": "the write half of an apply"},
    )


def write_decision(request: Request, root: str, operation: str = "apply"):
    """Return the ``write_files`` decision for a workspace root.

    Gated inside the call rather than in the 202, which is the download's arrangement
    and is the honest one: the user has already answered the question that mattered
    ("rewrite this project"), and a second popup for the same rewrite is the
    over-prompting that makes people approve without reading. What the 202 carries is
    ``write_action``, so the user can see before answering that files are involved at
    all.

    The refusal is a 403 rather than a second question for the same reason: the user
    said yes to the rewrite, and no to the write, and there is no third answer to ask
    for. The message names the folder, because "refused" without a path is not
    something anybody can act on.

    Args:
        request: Used to reach the gate and the trust manager.
        root: The workspace root.
        operation: The operation the decision is for, for the log.

    Returns:
        An allowed ``write_files`` decision.

    Raises:
        HTTPException: 403 when the gate does not permit writing there.
    """
    action = write_action(root, operation)
    _threat, decision = gate(request, action)
    if not decision.allowed:
        audit(request, BLOCKED, action.target, decision, operation=operation)
        raise HTTPException(
            status_code=403,
            detail=f"writing to {root} is refused: {decision.reason}",
        )
    return decision