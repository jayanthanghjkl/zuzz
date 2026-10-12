"""Face API — chat routes.

HTTP and WebSocket endpoints for talking to the brain. Every request goes
through the Thalamus; nothing here calls a model directly.

**A chat reply can carry actions, and here is where they happen.** The model
writes ```` ```action ```` blocks; :mod:`face.api.chat_actions` resolves each one,
has the Amygdala score it, has the gate decide it, and only then calls the Body.
Nothing in this module executes anything — it parses, dispatches and reports, so
there is exactly one place in the codebase where a chat turn can touch the
machine, and that place is the one with the gate in it.

Both surfaces return the same shape. ``POST /api/chat`` returns
``{response, actions: {...}}``. The WebSocket streams tokens, emits ``done``, and
then emits an ``actions`` event carrying the same envelope plus the authoritative
``prose``, so the client can replace what it streamed with the reply the user
should actually read.

Phase 4.5: Responses are enhanced with thinking visualization, confidence scoring,
context continuation, proactive suggestions, self-critique, and mood-adaptive tone.
These enhancements are included in the response and streamed as events.

**A finished turn is remembered.** Both surfaces hand the exchange to the
Hippocampus, which is the only way anything reaches the memory store at all — the
region has no listener of its own and no way to notice a conversation happened. The
call is last, and every exception is swallowed: a reply the user is reading must
not be withheld because a buffer append failed, and a machine that cannot answer a
question is a worse failure than one that cannot remember it.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from brain.cerebrum.cerebrum import CerebrumError, InferenceRefused, EnhancedResponse

from face.api.chat_actions import ChatActionRunner

router = APIRouter()

_INTERNET_CACHE: Dict[str, Any] = {"value": True, "checked_at": 0.0}
INTERNET_TTL = 30.0


class ChatRequest(BaseModel):
    """Body of a chat message."""

    message: str = Field(..., min_length=1, max_length=8000)
    level: Optional[int] = Field(
        default=None, ge=1, le=5, description="Force a routing level instead of auto"
    )


class ChatConfirmRequest(BaseModel):
    """Body of an answer to a confirmation the gate asked for.

    Attributes:
        confirmation_id: The id from the ``pending`` entry in the actions envelope.
        approval: Whether the user approved it.
    """

    confirmation_id: str = Field(..., min_length=1, max_length=200)
    approval: bool = Field(..., description="True approves, False declines")


async def internet_available() -> bool:
    """Check outbound connectivity, cached briefly to avoid per-message cost.

    Returns:
        True when a cloud provider is likely reachable.
    """
    if time.time() - _INTERNET_CACHE["checked_at"] < INTERNET_TTL:
        return bool(_INTERNET_CACHE["value"])
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.get("https://api.groq.com", timeout=3.0)
        value = True
    except Exception:
        value = False
    _INTERNET_CACHE.update(value=value, checked_at=time.time())
    return value


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


def remember_turn(request: Any, user_message: str, reply: str) -> None:
    """Hand one finished exchange to the Hippocampus.

    Never raises. The Hippocampus refuses on an empty message, and a refusal here
    would surface as a failed chat — so an exchange with nothing worth storing is
    simply not stored, which is what the refusal means anyway.

    The user message is what goes in, not the routed one. The model's own words are
    an answer derived from a prompt; storing them as though the user had said them
    is how a memory store fills up with the assistant talking to itself.

    Args:
        request: A ``Request`` or a ``WebSocket``. Both carry ``.app``, and the
            WebSocket path is the one the UI uses.
        user_message: What the user typed.
        reply: What ZUZZ answered, with every action fence already stripped.

    Returns:
        Nothing.
    """
    memory = getattr(request.app.state, "hippocampus", None)
    if memory is None:
        return
    try:
        memory.remember_conversation(user_message, reply, context="chat")
    except Exception:
        return


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
            enhanced: EnhancedResponse = await thalamus.cerebrum.think_enhanced(message, payload.level)
            result = enhanced.to_dict()
            result.update({"reason": "forced level", "degraded": False})
        else:
            # Route normally, then enhance
            result = await thalamus.route(message, await internet_available())
            # Enhance the reply
            raw_reply = result.get("reply", "")
            enhanced = thalamus.cerebrum.enhance_response(message, raw_reply)
            result["enhancement"] = enhanced.to_dict()
    except InferenceRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CerebrumError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    runner = ChatActionRunner(request.app.state)
    envelope = await runner.run_reply(str(result.get("reply", "") if "reply" in result else result.get("response", "")))
    result["response"] = envelope["prose"]
    result["actions"] = _wire(envelope)
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
        return _wire(await runner.confirm(payload.confirmation_id, payload.approval))
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


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stream a chat reply with enhancements, then what it did, over a WebSocket.

    Expects ``{"message": str, "level": int | None}`` and emits, in order:
    ``token`` events, ``thinking`` events, ``confidence`` events,
    ``suggestion`` events, one ``done``, and — **only if the reply asked for
    anything** — one ``actions`` event after it.

    The ``actions`` event arrives *after* ``done`` deliberately. It carries the
    authoritative ``prose``, which is the reply with every action fence removed.
    Tokens are forwarded verbatim while they arrive because waiting to parse
    would mean the UI shows nothing until the model finishes, which is the
    latency the streaming path exists to remove; the client replaces what it
    streamed with ``prose`` when the event lands, so raw JSON is on screen for
    the length of one reply and never in the user's scrollback.

    Args:
        websocket: The accepted connection.
    """
    await websocket.accept()
    thalamus = websocket.app.state.thalamus
    try:
        while True:
            data = await websocket.receive_json()
            message = str(data.get("message", "")).strip()
            if not message:
                await websocket.send_json({"type": "error", "content": "Empty message"})
                continue
            collected = ""
            try:
                # Use streaming with thinking events
                async for event in thalamus.cerebrum.stream(message):
                    if event.get("type") == "token":
                        collected += str(event.get("content", ""))
                    await websocket.send_json(event)
            except InferenceRefused as exc:
                await websocket.send_json(
                    {"type": "refused", "content": str(exc), "status": 409}
                )
                continue
            except CerebrumError as exc:
                await websocket.send_json({"type": "error", "content": str(exc)})
                continue

            # Enhance the collected reply for confidence, suggestions, critique
            enhanced = thalamus.cerebrum.enhance_response(message, collected)

            # Emit confidence event
            if enhanced.confidence:
                await websocket.send_json({
                    "type": "confidence",
                    "score": enhanced.confidence.score,
                    "label": enhanced.confidence.label,
                    "factors": enhanced.confidence.factors,
                })

            # Emit suggestion events
            for suggestion in enhanced.suggestions:
                await websocket.send_json({
                    "type": "suggestion",
                    "text": suggestion.text,
                    "category": suggestion.category,
                    "confidence": suggestion.confidence,
                    "action": suggestion.action,
                })

            # Emit mood event — what was detected and how the reply
            # was reshaped by it. Emitted before the variation
            # metadata so the UI can show the indicator first.
            from brain.cerebellum.emotion_responder import mood_hint

            await websocket.send_json({
                "type": "mood",
                "detected": enhanced.mood,
                "adjustment": mood_hint(enhanced.mood),
            })

            # Emit variation/mood metadata
            await websocket.send_json({
                "type": "variation",
                "mood": enhanced.mood,
                "meta": enhanced.variation_meta,
            })

            runner = ChatActionRunner(websocket.app.state)
            envelope = await runner.run_reply(enhanced.reply)
            # Remembered before the branch below, because that branch ``continue``s
            # and this is the surface the UI actually uses: remembering only on the
            # turns that carried an action would mean an ordinary conversation left
            # nothing behind at all.
            remember_turn(websocket, message, str(envelope["prose"]))
            if not envelope["has_actions"] and not envelope["warnings"]:
                # A conversational reply: no event, so the client has nothing to
                # render. Emitting an empty one would push a badge-less event
                # through the UI on every single message.
                await websocket.send_json({"type": "done", "prose": envelope["prose"]})
                continue
            await websocket.send_json(
                {"type": "actions", "prose": envelope["prose"], **envelope}
            )
    except WebSocketDisconnect:
        return
