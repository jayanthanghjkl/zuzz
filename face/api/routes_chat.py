"""HTTP chat, classification and approvals; shared model/action contracts.

Every auto-routed message uses the Thalamus. Tool JSON is executed before any
cosmetic enhancement. The WebSocket (chat_socket.py) emits optional action
outcomes followed by exactly one completion after the action runner finishes.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from brain.cerebrum.cerebrum import CerebrumError, InferenceRefused

from face.api.chat_actions import ChatActionRunner
from face.api.chat_contracts import ChatRequest, ChatConfirmRequest, remember_turn
from face.api.chat_socket import websocket_chat
from face.api.chat_presentation import enhancement, record_tool_results

router = APIRouter()

async def internet_available() -> bool:
    """Permit configured cloud attempts; each transport determines reachability.

    Failure to reach one vendor must not disable every other vendor. This is a
    routing hint, not a claim that an internet connectivity test succeeded.
    """
    return True


def _wire(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Narrow an orchestrator envelope to the shape the API publishes.

    :meth:`ChatActionRunner.run_reply` also returns ``prose`` and
    ``has_actions``, which are useful to the WebSocket and internal to the
    orchestrator. Publishing the whole dict would freeze that internal shape into
    the contract, so the six public keys are named here instead.

    Args:
        envelope: Whatever the runner returned.

    Returns:
        ``{executed, blocked, pending, errors, warnings, summary}``.
    """
    return {key: envelope[key] for key in _ACTION_KEYS}


#: The action keys every chat response carries, in the order they are documented.
_ACTION_KEYS = ("executed", "blocked", "pending", "errors", "warnings", "summary")


@router.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> Dict[str, Any]:
    """Answer a chat message, and do anything it asked to be done.

    Args:
        payload: The user message and an optional forced level.
        request: Used to reach the shared Thalamus instance.

    Returns:
        The answer plus routing metadata, with ``response`` holding the prose
        the user should read — every action fence stripped — and ``actions``
        holding what happened. Includes ``enhancement`` with thinking, confidence,
        suggestions, critique, and variation metadata.

    Raises:
        HTTPException: 400 for an empty message, 409 when the machine is stopped
            or the requested level cannot run, 503 when all backends fail.
    """
    thalamus = request.app.state.thalamus
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        if payload.level is not None:
            blocked = thalamus.check_level_allowed(payload.level)
            if blocked:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"level {payload.level} was requested but cannot run now — "
                        f"{blocked}. Omit `level` to let ZUZZ choose."
                    ),
                )
            # Use enhanced thinking for forced levels too
            result = await thalamus.cerebrum.think(message, payload.level)
            result.update({"reason": "forced level", "degraded": False})
        else:
            # Route normally, then enhance
            result = await thalamus.route(message, await internet_available())
    except InferenceRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CerebrumError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    runner = ChatActionRunner(request.app.state)
    envelope = await runner.run_reply(str(result.get("reply", "") if "reply" in result else result.get("response", "")))
    result["response"] = envelope["prose"]
    result["actions"] = _wire(envelope)
    enhanced = enhancement(thalamus.cerebrum, message, envelope["prose"])
    if enhanced is not None:
        result["enhancement"] = enhanced.to_dict()
    record_tool_results(request.app.state, envelope)
    remember_turn(request, message, str(envelope["prose"]))
    return result


@router.post("/api/chat/confirm")
async def confirm_action(
    payload: ChatConfirmRequest, request: Request
) -> Dict[str, Any]:
    """Answer a confirmation the gate asked for, and act on the answer.

    The action is re-decided here rather than upgraded blindly, so a stop that
    landed while the popup was open still blocks, and the id is single-use so a
    replayed approval cannot run the action twice.

    Args:
        payload: The confirmation id and whether the user approved it.
        request: Used to reach the shared collaborators.

    Returns:
        The same envelope shape as ``/api/chat``.

    Raises:
        HTTPException: 400 for an unknown, expired or already-used id, 409 when
            the stop is engaged.
    """
    runner = ChatActionRunner(request.app.state)
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    try:
        envelope = await runner.confirm(payload.confirmation_id, payload.approval)
        record_tool_results(request.app.state, envelope)
        return _wire(envelope)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/classify")
async def classify(payload: ChatRequest, request: Request) -> Dict[str, Any]:
    """Classify a message without sending it to any model.

    Args:
        payload: The message to classify.
        request: Used to reach the shared Thalamus instance.

    Returns:
        The routing decision.
    """
    thalamus = request.app.state.thalamus
    return thalamus.classify(payload.message, await internet_available())


router.websocket("/ws")(websocket_chat)
