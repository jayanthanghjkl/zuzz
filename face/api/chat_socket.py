"""WebSocket chat: validate, route, execute raw protocol, then complete once."""

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from brain.cerebrum.cerebrum import CerebrumError, InferenceRefused
from .chat_actions import ChatActionRunner
from .chat_contracts import ChatRequest, remember_turn
from .chat_presentation import enhancement_events, record_tool_results


async def websocket_chat(websocket: WebSocket) -> None:
    """Stream a chat reply with enhancements, then what it did, over a WebSocket.

    Expects ``{"message": str, "level": int | None}`` and emits, in order:
    ``token`` events, ``thinking`` events, ``confidence`` events,
    ``suggestion`` events, an optional ``actions`` event, then exactly one
    ``done`` event after all actions have settled.

    The ``actions`` event arrives *before* ``done`` so the UI stays busy. It carries the
    authoritative ``prose``, which is the reply with every action fence removed.
    Tokens are forwarded verbatim while they arrive because waiting to parse
    would mean the UI shows nothing until the model finishes, which is the
    latency the streaming path exists to remove; the client replaces what it
    streamed with ``prose`` when the event lands, so raw JSON is on screen for
    the length of one reply and never in the user's scrollback.

    Args:
        websocket: The accepted connection.
    """
    from . import routes_chat
    await websocket.accept()
    thalamus = websocket.app.state.thalamus
    try:
        while True:
            try:
                payload = ChatRequest.model_validate(await websocket.receive_json())
                message = payload.message.strip()
                if not message:
                    raise ValueError("Message cannot be empty")
            except (ValidationError, ValueError) as exc:
                await websocket.send_json({"type": "error", "content": str(exc)})
                continue
            collected = ""
            completion = {}
            try:
                options = {"internet_available": await routes_chat.internet_available()}
                if payload.level is not None:
                    options["level"] = payload.level
                async for event in thalamus.route_stream(message, **options):
                    if event.get("type") == "done":
                        completion = event
                        continue
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
            runner = ChatActionRunner(websocket.app.state)
            envelope = await runner.run_reply(collected)
            record_tool_results(websocket.app.state, envelope)
            for event in enhancement_events(thalamus.cerebrum, message, envelope["prose"]):
                await websocket.send_json(event)

            # Remembered before the branch below, because that branch ``continue``s
            # and this is the surface the UI actually uses: remembering only on the
            # turns that carried an action would mean an ordinary conversation left
            # nothing behind at all.
            remember_turn(websocket, message, str(envelope["prose"]))
            if envelope["has_actions"] or envelope["warnings"]:
                await websocket.send_json(
                    {"type": "actions", **envelope}
                )
            await websocket.send_json(
                {**completion, "type": "done", "prose": envelope["prose"]}
            )
    except WebSocketDisconnect:
        return
