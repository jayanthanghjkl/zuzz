"""Shared chat request contracts and best-effort long-term memory recording."""

from typing import Any, Optional
from pydantic import BaseModel, Field


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


