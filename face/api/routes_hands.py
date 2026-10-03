"""Face API — desktop operation routes.

Every desktop operation the UI can ask for, all of them routed through the
permission gate before anything reaches the machine. The flow is the same as the
file routes, and deliberately so: a user who has learned to read one set of
confirmation prompts can read the other.

1. Build the :class:`~brain.prefrontal_cortex.action.Action`, with the target
   the Body will actually act on — a coordinate as ``"100,200"``, an application
   as its resolved executable.
2. Have the Amygdala score it.
3. Have the gate decide at the current trust level.
4. ``blocked`` → 403 (409 when the kill switch is engaged). ``ask`` → 202 with a
   ``confirmation_id``. ``auto`` → execute and return 200.

A confirmation is answered by re-posting the same operation with ``confirmed:
true`` and the ``confirmation_id``. The parked action is re-decided at that
moment — a stop that landed while the popup was open still blocks — and only
then upgraded to allowed.

**Resolution happens before the gate, not after.** The whitelist maps ``chrome``
to ``chrome.exe``, and the decision is made about ``chrome.exe``. If the gate saw
the alias and the Body acted on the executable, an alias could smuggle in
something different.

Status codes: 200 success, 202 awaiting confirmation, 400 bad request, 403
blocked by rule or classification, 404 no such window or process, 409 kill
switch or conflict, 500 internal (details logged, generic message returned).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.permission_gate import PermissionGate
from core.audit import BLOCKED, CONFIRMATION_GRANTED, CONFIRMATION_REQUESTED

from body.hands import action_classifier
from body.hands.exceptions import (
    ActuatorError,
    DangerousHotkeyRefused,
    DangerousTextRefused,
    DecisionRefused,
    DryRunRefused,
    FailSafeTriggered,
    HandsRefused,
    InvalidInputRefused,
    KillSwitchRefused,
    ProtectedRefused,
    TargetNotFoundRefused,
)
from body.hands.hands import Hands
from body.hands.mouse import coordinate_target

from face.api.file_confirmations import confirmation_payload

router = APIRouter()


# ---------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------


class HandsRequest(BaseModel):
    """Fields every desktop operation accepts."""

    dry_run: bool = Field(False, description="Preview without touching the desktop")
    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")


class PointRequest(HandsRequest):
    """An operation at a single coordinate."""

    x: int
    y: int
    button: str = Field("left", description="left, right or middle")
    clicks: int = Field(1, description="1 for a click, 2 for a double click")
    duration: float = Field(0.5, description="Seconds the movement takes")


class DragRequest(HandsRequest):
    """An operation with two coordinates."""

    start_x: int
    start_y: int
    end_x: int
    end_y: int
    duration: float = Field(0.5, description="Seconds the drag takes")


class ScrollRequest(HandsRequest):
    """A scroll, at the cursor unless told otherwise."""

    clicks: int = Field(..., description="Wheel notches; negative scrolls down")
    x: int = Field(None, description="Horizontal coordinate; the cursor when null")
    y: int = Field(None, description="Vertical coordinate; the cursor when null")


class TypeRequest(HandsRequest):
    """Typed text."""

    text: str = Field(..., description="The string to type")
    interval: float = Field(0.01, description="Seconds between keystrokes")


class KeyRequest(HandsRequest):
    """One key."""

    key: str
    presses: int = Field(1, description="How many times to press it")


class KeysRequest(HandsRequest):
    """A key sequence or combination, in any order."""

    keys: list = Field(..., description="Key names")


class ClipboardWriteRequest(HandsRequest):
    """Clipboard contents to set."""

    text: str


class AppRequest(HandsRequest):
    """An application to open or close."""

    name: str = Field(..., description="A whitelist alias, a title, or a process")


class ProcessRequest(HandsRequest):
    """A process to terminate."""

    pid: int


class WindowRequest(HandsRequest):
    """A window to act on."""

    window: str = Field(..., description="A handle, an exact title, or a fragment")
    state: str = Field(
        "", description="minimise, maximise or restore, for set_window_state"
    )
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _gate(request: Request, category: str, target: str, dry_run: bool = False):
    """Score and decide one action.

    Args:
        request: Used to reach the trust level.
        category: The action category.
        target: What the action acts on, already resolved to the form the Body
            will use.
        dry_run: Whether the action is a preview.

    Returns:
        The action, its threat assessment and the decision.
    """
    level = request.app.state.trust.current()
    action = Action(
        category=ActionCategory(category), target=target, dry_run=dry_run
    )
    threat = ThreatDetector().assess(action)
    decision = PermissionGate().decide(action, level, threat)
    return action, threat, decision


def _audit(
    request: Request,
    event: str,
    category: str,
    target: str,
    decision: PermissionDecision,
) -> None:
    """Record a safety event in the audit log."""
    audit = getattr(request.app.state, "audit", None)
    if audit is None:
        return
    audit.record(
        event,
        decision.reason,
        category=category,
        target=target,
        level=decision.decision,
        rule_id=decision.rule_id or "",
        trust_level=decision.trust_level,
    )


def _audit_gate_refusal(request: Request, reason: str) -> None:
    """Record a refusal that happened before any decision existed.

    A never-launch command is refused while resolving the target, so there is no
    decision to record — but the attempt is exactly the kind of thing the audit
    log exists for, so it is written with the reason and no category.
    """
    audit = getattr(request.app.state, "audit", None)
    if audit is not None:
        audit.record(BLOCKED, reason)


def _refuse(exc: Exception) -> HTTPException:
    """Map a Hands refusal onto an HTTP status.

    The hierarchy does the work: a stop outranks the action, and a target ZUZZ
    must never touch is a 403 whatever the trust level was.

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise.
    """
    if isinstance(exc, KillSwitchRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProtectedRefused):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (DecisionRefused, DryRunRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, TargetNotFoundRefused):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidInputRefused):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, FailSafeTriggered):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ActuatorError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


def _release_stuck_keys(hands: Hands) -> None:
    """Release any key ZUZZ is holding after a failed operation.

    A stuck modifier is a safety problem, so cleanup runs on every failure path
    rather than relying on the caller to ask for it.
    """
    try:
        hands.release_held_keys()
    except Exception:
        pass


async def _run(
    request: Request,
    body: HandsRequest,
    category: str,
    target: str,
    execute: Callable[[PermissionDecision], Any],
    preview: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """Gate, confirm and run one desktop operation.

    Args:
        request: Used to reach the app's collaborators.
        body: The request, including confirmation evidence.
        category: The action category for the gate.
        target: The action's target, already resolved.
        execute: The Hands call, invoked only with an allowed decision.
        preview: What the confirmation popup should show.

    Returns:
        The API response: a plan, a confirmation prompt, or the result.

    Raises:
        HTTPException: Mapped from the Hands refusals.
    """
    hands: Hands = request.app.state.hands
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    action, threat, decision = _gate(request, category, target, body.dry_run)
    if body.dry_run:
        return {
            "status": "dry_run",
            "plan": preview(),
            "decision": decision.to_dict(),
            "threat": threat.to_dict(),
        }
    if decision.blocked:
        _audit(request, BLOCKED, category, target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if decision.requires_confirmation and not body.confirmed:
        confirmation_id = request.app.state.confirmations.create(
            action, decision, preview()
        )
        _audit(request, CONFIRMATION_REQUESTED, category, target, decision)
        return JSONResponse(
            status_code=202,
            content={
                "status": "confirmation_required",
                **confirmation_payload(
                    confirmation_id, target, decision, threat, preview()
                ),
            },
        )
    if decision.requires_confirmation:
        decision = await _confirm(request, body, category, target)
    try:
        result = execute(decision)
    except Exception as exc:
        _release_stuck_keys(hands)
        # Everything the Body refuses derives from HandsRefused, so that base
        # class is the check. An earlier version listed the concrete subclasses
        # instead, and a newly added one — InvalidInputRefused — silently became
        # a 500 instead of a 400. The list would have to be kept in step by hand
        # forever; the base class cannot drift.
        if isinstance(exc, (HandsRefused, ValueError)):
            _audit(
                request,
                BLOCKED,
                category,
                target,
                replace(decision, reason=str(exc)),
            )
            raise _refuse(exc) from exc
        request.app.state.logger.log(
            "face", "hands_api_error", category=category, target=target, error=str(exc)
        )
        raise HTTPException(status_code=500, detail="internal error") from exc
    # An executed action is not a safety event, so it goes to the activity log
    # rather than the audit log. The audit log records what was refused and what
    # was confirmed; putting routine clicks in it would bury both.
    request.app.state.logger.log(
        "face", "hands_api_ok", category=category, target=target
    )
    return {"status": "ok", "result": result, "decision": decision.to_dict()}


async def _confirm(
    request: Request, body: HandsRequest, category: str, target: str
) -> PermissionDecision:
    """Answer a parked confirmation.

    The action is re-decided here rather than upgraded blindly, so a stop that
    landed while the popup was open still blocks.

    Args:
        request: Used to reach the app's collaborators.
        body: The request carrying the confirmation evidence.
        category: The action category.
        target: The action's target.

    Returns:
        The upgraded decision.

    Raises:
        HTTPException: 404 for an unknown confirmation, 409 for a stale one.
    """
    confirmations = request.app.state.confirmations
    if not body.confirmation_id:
        raise HTTPException(status_code=400, detail="confirmation_id is required")
    parked = confirmations.get(body.confirmation_id)
    if parked is None:
        raise HTTPException(status_code=404, detail="unknown confirmation")
    if parked.consumed:
        raise HTTPException(
            status_code=409, detail="confirmation already used; ask again"
        )
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    level = request.app.state.trust.current()
    decision = PermissionGate().decide(parked.action, level)
    if decision.blocked or decision.requires_confirmation:
        raise HTTPException(
            status_code=403,
            detail=f"still not permitted at level {level}: {decision.reason}",
        )
    confirmations.consume(body.confirmation_id)
    _audit(request, CONFIRMATION_GRANTED, category, target, decision)
    return decision


def _plan(description: str, **fields: Any) -> Callable[[], Dict[str, Any]]:
    """Return a preview callable describing what an action would do.

    Args:
        description: One line in plain language.
        **fields: Structured detail for the popup.

    Returns:
        A callable the dispatcher can hand to the confirmation store.
    """
    return lambda: {"description": description, **fields}


# ---------------------------------------------------------------------
# Read: what is on the screen
# ---------------------------------------------------------------------


@router.get("/api/hands/position")
async def get_position(request: Request) -> Dict[str, Any]:
    """Return the cursor position, through the gate."""
    hands: Hands = request.app.state.hands
    category = action_classifier.category_for("get_position")
    _, threat, decision = _gate(request, category, "")
    if decision.blocked:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {
        "status": "ok",
        "result": hands.get_position(decision),
        "decision": decision.to_dict(),
        "threat": threat.to_dict(),
    }


@router.get("/api/hands/windows")
async def list_windows(request: Request) -> Dict[str, Any]:
    """Return every visible window, through the gate."""
    hands: Hands = request.app.state.hands
    category = action_classifier.category_for("list_windows")
    _, _, decision = _gate(request, category, "")
    if decision.blocked:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {"status": "ok", "result": hands.list_windows(decision)}


@router.get("/api/hands/active-window")
async def get_active_window(request: Request) -> Dict[str, Any]:
    """Return the focused window, through the gate."""
    hands: Hands = request.app.state.hands
    category = action_classifier.category_for("get_active_window")
    _, _, decision = _gate(request, category, "")
    if decision.blocked:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {"status": "ok", "result": hands.get_active_window(decision)}


@router.get("/api/hands/apps")
async def list_apps(request: Request) -> Dict[str, Any]:
    """Return the running processes, through the gate."""
    hands: Hands = request.app.state.hands
    category = action_classifier.category_for("list_running_apps")
    _, _, decision = _gate(request, category, "")
    if decision.blocked:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {"status": "ok", "result": hands.list_running_apps(decision)}


# ---------------------------------------------------------------------
# The decision-free endpoints
# ---------------------------------------------------------------------


@router.get("/api/hands/layout")
async def layout(request: Request) -> Dict[str, Any]:
    """Return the display layout.

    Configuration the user reads, so it asks nothing — exactly like showing the
    current trust level.
    """
    return {"status": "ok", "layout": request.app.state.hands.describe_layout()}


@router.get("/api/hands/policy")
async def policy(request: Request) -> Dict[str, Any]:
    """Return the application and hotkey policy.

    Showing the user exactly what ZUZZ will refuse is the opposite of an action.
    """
    return {"status": "ok", "policy": request.app.state.hands.describe_policy()}


@router.post("/api/hands/release-keys")
async def release_keys(request: Request) -> Dict[str, Any]:
    """Release every key ZUZZ is holding.

    The one input endpoint that takes no decision. It undoes damage ZUZZ itself
    caused: the operation that would have authorised it has already failed, and
    requiring a fresh decision to release a stuck modifier would leave the user's
    keyboard in a state they never chose.
    """
    return {"status": "ok", "released": request.app.state.hands.release_held_keys()}


# ---------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------


@router.post("/api/hands/move")
async def move(request: Request, body: PointRequest) -> Dict[str, Any]:
    """Move the pointer, through the gate."""
    hands: Hands = request.app.state.hands
    target = coordinate_target(body.x, body.y)
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        target,
        lambda d: hands.move(d, body.x, body.y, body.duration),
        _plan(f"Move the pointer to ({body.x}, {body.y})", x=body.x, y=body.y),
    )


@router.post("/api/hands/click")
async def click(request: Request, body: PointRequest) -> Dict[str, Any]:
    """Click at a point, through the gate."""
    hands: Hands = request.app.state.hands
    target = coordinate_target(body.x, body.y)
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        target,
        lambda d: hands.click(
            d, body.x, body.y, button=body.button, clicks=body.clicks
        ),
        _plan(
            f"{'Double-' if body.clicks > 1 else ''}Click "
            f"{body.button} at ({body.x}, {body.y})",
            x=body.x,
            y=body.y,
        ),
    )


@router.post("/api/hands/drag")
async def drag(request: Request, body: DragRequest) -> Dict[str, Any]:
    """Drag between two points, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        coordinate_target(body.start_x, body.start_y),
        lambda d: hands.drag(
            d, (body.start_x, body.start_y), (body.end_x, body.end_y), body.duration
        ),
        _plan(
            f"Drag from ({body.start_x}, {body.start_y}) to "
            f"({body.end_x}, {body.end_y})",
            start_x=body.start_x,
            start_y=body.start_y,
            end_x=body.end_x,
            end_y=body.end_y,
        ),
    )


@router.post("/api/hands/scroll")
async def scroll(request: Request, body: ScrollRequest) -> Dict[str, Any]:
    """Scroll the wheel, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        "cursor" if body.x is None else coordinate_target(body.x, body.y),
        lambda d: hands.scroll(d, body.clicks, x=body.x, y=body.y),
        _plan(f"Scroll {body.clicks} notches", clicks=body.clicks),
    )


# ---------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------


@router.post("/api/hands/type")
async def type_text(request: Request, body: TypeRequest) -> Dict[str, Any]:
    """Type a string, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        body.text[:80],
        lambda d: hands.type_text(d, body.text, body.interval),
        _plan(f"Type {len(body.text)} characters", text=body.text[:200]),
    )


@router.post("/api/hands/press")
async def press(request: Request, body: KeyRequest) -> Dict[str, Any]:
    """Press a key, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        body.key,
        lambda d: hands.press(d, body.key, body.presses),
        _plan(f"Press {body.key}", key=body.key),
    )


@router.post("/api/hands/press-keys")
async def press_keys(request: Request, body: KeysRequest) -> Dict[str, Any]:
    """Press a key sequence, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        ",".join(body.keys),
        lambda d: hands.press_keys(d, body.keys),
        _plan(f"Press {' then '.join(body.keys)}", keys=body.keys),
    )


@router.post("/api/hands/hotkey")
async def hotkey(request: Request, body: KeysRequest) -> Dict[str, Any]:
    """Press a key combination, through the gate.

    The always-blocked combinations and ZUZZ's own emergency stop never reach
    the gate — the Body refuses them absolutely, so there is no confirmation to
    show and no trust level at which they become a question.
    """
    hands: Hands = request.app.state.hands
    from body.hands.hotkey_policy import confirmable_hotkey, guard_hotkey

    try:
        guard_hotkey(body.keys)
    except Exception as exc:
        raise _refuse(exc) from exc
    consequence = confirmable_hotkey(body.keys)
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        "+".join(body.keys),
        lambda d: hands.hotkey(d, body.keys),
        _plan(
            f"Press {'+'.join(body.keys)}"
            + (f" — this {consequence}" if consequence else ""),
            keys=body.keys,
            consequence=consequence or "",
        ),
    )


@router.post("/api/hands/hold-key")
async def hold_key(request: Request, body: KeyRequest) -> Dict[str, Any]:
    """Press and hold a key, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        body.key,
        lambda d: hands.hold_key(d, body.key),
        _plan(
            f"Hold {body.key} down", key=body.key, warning="release it afterwards"
        ),
    )


@router.post("/api/hands/release-key")
async def release_key(request: Request, body: KeyRequest) -> Dict[str, Any]:
    """Release a held key, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.mouse_category(),
        body.key,
        lambda d: hands.release_key(d, body.key),
        _plan(f"Release {body.key}", key=body.key),
    )


# ---------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------


@router.get("/api/hands/clipboard")
async def read_clipboard(request: Request) -> Dict[str, Any]:
    """Read the clipboard, through the gate.

    Credential-shaped content escalates the read to ``access_credentials``, which
    asks at every trust level. The content that triggered the escalation is not
    what classifies it — the API asks the Body what category the content needs,
    then builds the decision for that category, so a read the gate approved as
    ``read_files`` can never quietly return a secret.
    """
    hands: Hands = request.app.state.hands
    from body.hands.text_scanner import looks_like_credential

    probe = hands.clipboard._read_text()
    category = action_classifier.clipboard_read_category(looks_like_credential(probe))
    _, _, decision = _gate(request, category, "clipboard")
    if decision.blocked:
        raise HTTPException(status_code=403, detail=decision.reason)
    if decision.requires_confirmation:
        raise HTTPException(
            status_code=403,
            detail=(
                f"the clipboard holds {category.replace('_', ' ')}, which asks "
                f"at every trust level: {decision.reason}"
            ),
        )
    result = hands.clipboard_read(decision)
    return {
        "status": "ok",
        "text": result["text"],
        "category": result["category"],
        "credential_finding": (
            result["credential_finding"]["id"]
            if result["credential_finding"]
            else None
        ),
    }


@router.post("/api/hands/clipboard")
async def write_clipboard(
    request: Request, body: ClipboardWriteRequest
) -> Dict[str, Any]:
    """Replace the clipboard contents, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.category_for("clipboard_write"),
        body.text[:80],
        lambda d: hands.clipboard_write(d, body.text),
        _plan(
            f"Put {len(body.text)} characters on the clipboard",
            text=body.text[:200],
        ),
    )


@router.delete("/api/hands/clipboard")
async def clear_clipboard(request: Request) -> Dict[str, Any]:
    """Empty the clipboard, through the gate."""
    hands: Hands = request.app.state.hands
    category = action_classifier.category_for("clipboard_clear")
    body = HandsRequest()
    return await _run(
        request,
        body,
        category,
        "clipboard",
        lambda d: hands.clipboard_clear(d),
        _plan("Empty the clipboard"),
    )


# ---------------------------------------------------------------------
# Applications and processes
# ---------------------------------------------------------------------


@router.post("/api/hands/open-app")
async def open_app(request: Request, body: AppRequest) -> Dict[str, Any]:
    """Launch an application, through the gate.

    The target is resolved to an executable *before* the gate sees it, so the
    decision is made about ``chrome.exe`` rather than about the alias that
    produced it. An unknown application is not blocked — it maps to
    ``run_safe_commands``, which asks at every trust level.
    """
    hands: Hands = request.app.state.hands
    # Resolution can refuse outright — a never-launch command is one of those —
    # and that happens before the gate is consulted, which is the point. It is
    # caught here so the refusal becomes a 403 rather than escaping as a 500.
    try:
        resolved = action_classifier.resolve_target("app", body.name)
    except HandsRefused as exc:
        _audit_gate_refusal(request, str(exc))
        raise _refuse(exc) from exc
    return await _run(
        request,
        body,
        resolved["category"],
        resolved["executable"],
        lambda d: hands.open_app(d, body.name),
        _plan(
            f"Open {resolved['executable']}",
            executable=resolved["executable"],
            whitelisted=resolved["whitelisted"],
            note=resolved["reason"],
        ),
    )


@router.post("/api/hands/close-app")
async def close_app(request: Request, body: AppRequest) -> Dict[str, Any]:
    """Ask an application to close, through the gate.

    ``close_apps`` asks at every trust level: closing can lose unsaved work, and
    no level at which ZUZZ ends an application without being asked is one a user
    would agree to.
    """
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.app_close_category(),
        body.name,
        lambda d: hands.close_app(d, body.name),
        _plan(f"Close {body.name}", warning="unsaved work may be lost"),
    )


@router.post("/api/hands/kill-process")
async def kill_process(request: Request, body: ProcessRequest) -> Dict[str, Any]:
    """Terminate a process immediately, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.app_close_category(),
        str(body.pid),
        lambda d: hands.kill_process(d, body.pid),
        _plan(
            f"Terminate process {body.pid}",
            warning="unsaved work is lost without warning",
        ),
    )


# ---------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------


@router.post("/api/hands/focus-window")
async def focus_window(request: Request, body: WindowRequest) -> Dict[str, Any]:
    """Bring a window to the foreground, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.category_for("focus_window"),
        body.window,
        lambda d: hands.focus_window(d, body.window),
        _plan(f"Bring {body.window!r} to the front", window=body.window),
    )


@router.post("/api/hands/window-state")
async def window_state(request: Request, body: WindowRequest) -> Dict[str, Any]:
    """Minimise, maximise or restore a window, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.category_for("maximize"),
        body.window,
        lambda d: hands.set_window_state(d, body.window, body.state),
        _plan(f"{body.state or 'restore'} {body.window!r}", window=body.window),
    )


@router.post("/api/hands/resize-window")
async def resize_window(request: Request, body: WindowRequest) -> Dict[str, Any]:
    """Move and resize a window, through the gate."""
    hands: Hands = request.app.state.hands
    return await _run(
        request,
        body,
        action_classifier.category_for("resize_window"),
        body.window,
        lambda d: hands.resize_window(
            d, body.window, body.left, body.top, body.width, body.height
        ),
        _plan(
            f"Move {body.window!r} to ({body.left}, {body.top}) "
            f"at {body.width}x{body.height}",
            window=body.window,
        ),
    )