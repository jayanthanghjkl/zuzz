"""Face API — model management confirmations.

The permission flow for loading, unloading and swapping models, split out of
:mod:`face.api.routes_resources` the same way ``file_confirmations`` splits the
file flow out of ``routes_files``. The routes are then left doing routing and
nothing else.

The rule this module exists to make unbreakable: a model-management call reaches
the manager only if the Prefrontal Cortex allowed it, or if the user answered a
prompt that was raised for *that* call. There is no third path, and in particular
there is no path where a 202 — a status code — is mistaken for a permission.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from brain.amygdala.threat_detector import ThreatDetector
from brain.hypothalamus.exceptions import (
    BudgetExceededError,
    DecisionRequiredError,
    HotSwapRollbackError,
    ModelLoadError,
    ModelNotFoundError,
    ModelUnloadError,
    PermissionErrorForModel,
    ResourceUnavailableError,
)
from brain.hypothalamus.model_manager import MANAGE_MODELS
from brain.prefrontal_cortex.action import Action
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_matrix import AUTO
from core.audit import CONFIRMATION_GRANTED


def gate_for(request: Request) -> PermissionGate:
    """Return a gate reading the app's *current* kill switch.

    Built per request rather than stored on app state, because the switch is
    consulted before the matrix. A gate that captured the switch at startup would
    keep consulting an object the app has since replaced, and a stop the user
    engaged would not outrank a model load.

    Args:
        request: The incoming request.

    Returns:
        A gate bound to ``request.app.state.kill_switch``.
    """
    return PermissionGate(kill_switch=request.app.state.kill_switch)


def refuse(exc: Exception) -> HTTPException:
    """Map a Hypothalamus error onto an HTTP status.

    The mapping carries meaning rather than collapsing everything to 500. A full
    budget is a 429 — the system is healthy and full, and the caller's options
    are to wait, drop a model, or route to the cloud. A hot GPU or an
    unreachable backend is a 503. A 500 means ZUZZ is broken, and reporting it
    when the real answer is "the disk is full" trains the user to ignore the one
    status that matters.

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise.
    """
    detail: Dict[str, Any] = {"error": "internal_error", "reason": "internal error"}
    if isinstance(exc, PermissionErrorForModel):
        detail = {
            "error": "model_management_not_permitted",
            "reason": str(exc),
            "confirmable": exc.needs_confirmation,
        }
        status = 409
    elif isinstance(exc, BudgetExceededError):
        detail = {"error": "vram_budget_exceeded", "reason": str(exc)}
        status = 429
    elif isinstance(exc, ModelNotFoundError):
        detail = {"error": "model_not_found", "reason": str(exc)}
        status = 404
    elif isinstance(exc, (ModelLoadError, ModelUnloadError, HotSwapRollbackError)):
        detail = {"error": "model_backend_unavailable", "reason": str(exc)}
        status = 503
    elif isinstance(exc, ResourceUnavailableError):
        detail = {"error": "resource_unavailable", "reason": str(exc)}
        status = 503
    elif isinstance(exc, DecisionRequiredError):
        detail = {"error": "decision_required", "reason": str(exc)}
        status = 403
    else:
        status = 500
    return HTTPException(status_code=status, detail=detail)


def authorise(
    request: Request, operation: str, model: str, confirm_id: str
) -> Optional[Any]:
    """Resolve the permission question for one model-management call.

    The single choke point for the three mutating routes, and the only place a
    decision is made. It does exactly one of three things:

    * returns the decision from a parked confirmation the user has answered;
    * returns a response describing a freshly parked confirmation, meaning "not
      yet" — it executes nothing;
    * raises, for a verdict no confirmation can unlock.

    A refusal a human *could* approve is parked so the client can offer a dialog.
    One that could not raises, carrying ``confirmable: false`` so the UI knows not
    to offer a prompt that cannot change the answer.

    Args:
        request: Used to reach the gate, the trust manager and the confirmations.
        operation: What is being done, for the log and the dialog.
        model: The model being acted on.
        confirm_id: The confirmation this request answers, or empty.

    Returns:
        The ``PermissionDecision`` to act on, or the parked-confirmation response.

    Raises:
        HTTPException: 400 for an unknown or mismatched confirmation, 409 for a
            decision no confirmation can unlock.
    """
    confirmations = request.app.state.confirmations
    if confirm_id:
        return _answer(request, confirmations.pop(confirm_id), operation, model, confirm_id)

    action = _action(operation, model)
    threat = ThreatDetector().assess(action)
    decision = gate_for(request).decide(
        action, request.app.state.trust.current(), threat
    )
    if decision.allowed:
        return decision
    if not decision.requires_confirmation:
        raise refuse(PermissionErrorForModel(decision.reason, decision))
    return _park(confirmations, decision, threat, operation, model)


def _answer(
    request: Request,
    entry: Optional[Dict[str, Any]],
    operation: str,
    model: str,
    confirm_id: str,
) -> Any:
    """Turn an answered confirmation into a decision that permits the action.

    Args:
        request: Used to reach the gate, the trust manager and the audit trail.
        entry: The parked entry, or None when the id is unknown or expired.
        operation: What is being requested now.
        model: The model being acted on now.
        confirm_id: The id the caller quoted.

    Returns:
        A fresh decision with the ``ask`` upgraded to an allow.

    Raises:
        HTTPException: 400 when the id is unknown, or when the confirmation was
            raised for a different action than the one now requested.
    """
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown_confirmation", "reason": "unknown or expired confirmation_id"},
        )
    parked: Action = entry["action"]
    # The parked action must be the action being requested. Without this a
    # confirmation for one model could be spent on another, which would make the
    # dialog a suggestion rather than an answer.
    if str(parked.category) != MANAGE_MODELS or parked.target != model:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "confirmation_mismatch",
                "reason": f"this confirmation is for {parked.target}, not {model}",
            },
        )
    # Re-decided rather than reusing the parked verdict: the trust level or the
    # matrix may have changed while the dialog was open, and an approval given
    # under the old rules must not be spent under new ones.
    action = _action(operation, model)
    threat = ThreatDetector().assess(action)
    fresh = gate_for(request).decide(action, request.app.state.trust.current(), threat)
    if fresh.blocked:
        raise refuse(PermissionErrorForModel(fresh.reason, fresh))
    # The user has confirmed this specific action, so the "ask" becomes an
    # allow. Only "ask" is ever upgraded — a blocked decision cannot reach here.
    request.app.state.audit.record(
        CONFIRMATION_GRANTED,
        f"user approved {operation} {model} at trust level {fresh.trust_level}",
        category=MANAGE_MODELS,
        target=model,
    )
    return replace(fresh, allowed=True, requires_confirmation=False, decision=AUTO)


def _park(
    confirmations: Any, decision: Any, threat: Any, operation: str, model: str
) -> Dict[str, Any]:
    """Park a confirmation and return what the dialog renders."""
    confirmation_id = confirmations.create(
        decision.action,
        decision,
        {
            "file_count": None,
            "total_bytes": None,
            "total_human": f"{operation} {model}",
            "reversible": True,
        },
    )
    return {
        "status": "confirmation_required",
        "confirmation_id": confirmation_id,
        "operation": operation,
        "model": model,
        "trust_level": decision.trust_level,
        "decision": decision.decision,
        "reason": decision.reason,
        "threat": {
            "score": threat.score,
            "level": threat.level,
            "explanation": threat.explanation,
        },
    }


def _action(operation: str, model: str) -> Action:
    """Build the ``manage_models`` action for one operation."""
    return Action(
        category=MANAGE_MODELS, target=model, detail={"operation": operation}
    )
