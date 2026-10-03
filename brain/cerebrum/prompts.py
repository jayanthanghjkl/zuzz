"""Cerebrum — prompts.

Builds the system prompt from ZUZZ's personality configuration so tone and
verbosity stay in `config/personality.json` instead of being hardcoded here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import get_personality

BASE_IDENTITY = (
    "You are ZUZZ, a personal digital brain assistant running on Jayanthan's "
    "Windows laptop. You are direct, accurate and honest. If you are not sure "
    "of something, say so rather than guessing."
)

SAFETY_RULES = (
    "Never fabricate system commands, file paths or facts. Never claim an "
    "action was performed unless you actually performed it. Refuse requests "
    "that would harm the system or expose credentials."
)


def build_system_prompt(context: Optional[str] = None) -> str:
    """Compose the system prompt for a conversation.

    Args:
        context: Optional extra context, e.g. detected user activity.

    Returns:
        The full system prompt string.
    """
    personality: Dict[str, Any] = get_personality()
    tone = ", ".join(personality.get("tone", ["direct"]))
    max_sentences = personality.get("max_sentences_per_reply", 8)

    parts = [
        BASE_IDENTITY,
        f"Speaking style: {tone}. Keep replies under about {max_sentences} sentences "
        "unless the user asks for detail.",
        SAFETY_RULES,
    ]
    if context:
        parts.append(f"Current context: {context}")
    return "\n\n".join(parts)


def trim_history(messages: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """Trim a message list to the most recent ``limit`` entries.

    Keeps the history aligned on turn boundaries so a reply is never orphaned
    from the user message that triggered it.

    Args:
        messages: Conversation history oldest-first.
        limit: Maximum number of messages to keep.

    Returns:
        A new list with at most ``limit`` messages.
    """
    if limit <= 0 or len(messages) <= limit:
        return list(messages)
    trimmed = list(messages[-limit:])
    for index, message in enumerate(trimmed):
        if message.get("role") == "user":
            return trimmed[index:]
    return trimmed


def build_messages(
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    context: Optional[str] = None,
    history_limit: int = 20,
) -> List[Dict[str, str]]:
    """Assemble the full message list sent to a model.

    Args:
        user_message: The new user turn.
        history: Prior conversation, oldest-first.
        context: Optional situational context for the system prompt.
        history_limit: Maximum retained history messages.

    Returns:
        Messages ready to post to Ollama or a cloud provider.
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(context)}
    ]
    messages.extend(
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in trim_history(history or [], history_limit)
    )
    messages.append({"role": "user", "content": user_message})
    return messages
