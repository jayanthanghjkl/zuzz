"""Cerebrum — reading and validating one action block.

Everything :func:`~brain.cerebrum.action_parser.parse_actions` needs to turn a
fenced block into a call it is willing to act on, kept apart from the reply
splitting and the result types.

The separation matters because the two halves fail differently and must not be
able to hide each other. The block reader returns a *reason* instead of raising:
one malformed block must not discard the four good ones beside it. The call
builder is where schema validation happens, and it refuses an unknown tool name
outright rather than producing a call the registry does not have — a
``ToolCall`` naming a tool that cannot be reached would move the refusal to a
less visible place, whereas a problem string is something the user reads.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from brain.cerebrum.tools_coercion import ToolParamError
from brain.cerebrum.tools_validate import validate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from brain.cerebrum.action_parser import ToolCall

__all__ = [
    "ACTION_LANGUAGES",
    "FENCE",
    "MAX_ACTIONS_PER_REPLY",
    "build_call",
    "read_block",
    "tidy_prose",
]

#: Fence languages treated as an action block. A bare fence counts too, because
#: small local models drop the language tag roughly as often as they keep it —
#: but only if the body parses as a tool call, so ordinary code blocks in a
#: reply are left alone.
ACTION_LANGUAGES = frozenset({"", "action", "action json", "tool", "tool_call", "tool call"})

FENCE = re.compile(r"```(?P<lang>[^\n`]*)\r?\n?(?P<body>.*?)```", re.DOTALL)

#: Most actions one reply may ask for. A reply that wants twenty actions is
#: either confused or is being used to slip past a prompt, and either way it
#: should be refused rather than queued.
MAX_ACTIONS_PER_REPLY = 5


def read_block(body: str) -> Tuple[Any, str]:
    """Read the JSON object out of one fenced block.

    Args:
        body: The block's contents, which may carry a sentence of commentary
            before or after the object.

    Returns:
        ``(payload, "")`` where ``payload`` is the decoded object, or
        ``(None, reason)`` when there is nothing decodable in it.

    Raises:
        ToolParamError: Never. Failure is returned, not raised, because one bad
            block must not discard the others.
    """
    start = body.find("{")
    if start < 0:
        return None, "an action block contained no JSON object"
    end = body.rfind("}")
    if end < start:
        return None, "the JSON object in an action block is not closed"
    try:
        return json.loads(body[start : end + 1]), ""
    except json.JSONDecodeError as exc:
        return None, f"the JSON could not be parsed: {exc.msg} at line {exc.lineno}"


def build_call(
    payload: Any, problems: List[str], dropped: Optional[List[Dict[str, str]]] = None
) -> Optional["ToolCall"]:
    """Turn one decoded block into a validated call.

    Args:
        payload: Whatever the block decoded to.
        problems: Collected reasons a block could not be used, appended to.
        dropped: Structured records of the same failures, appended to. Carries
            the tool name the model used, so the caller can report which action
            failed rather than only that something did. Optional, so a caller
            that only wants the reason strings can pass None.

    Returns:
        The call, or None when the payload is not a usable tool call. An unknown
        tool name returns None rather than a call: there is nothing to execute,
        and a ``ToolCall`` whose tool the registry does not have would only move
        the refusal somewhere less visible than a problem the user reads.
    """
    from brain.cerebrum.action_parser import ToolCall

    def fail(reason: str, tool: str = "") -> None:
        """Record one unusable block in both the string and structured lists."""
        problems.append(reason)
        if dropped is not None:
            dropped.append({"tool": tool, "reason": reason})

    if not isinstance(payload, dict):
        fail("an action block was not a JSON object")
        return None
    name = payload.get("tool")
    if not isinstance(name, str) or not name.strip():
        fail("an action block named no tool")
        return None
    name = name.strip()
    params = payload.get("params", payload.get("arguments", {}))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        fail(f"{name}: params was not an object", name)
        return None
    explanation = payload.get("explanation", "")
    if not isinstance(explanation, str):
        explanation = str(explanation)
    try:
        return ToolCall(tool=name, params=validate(name, params), explanation=explanation)
    except ToolParamError as exc:
        fail(str(exc), name)
        return None


def tidy_prose(text: str) -> str:
    """Collapse the blank lines left behind by removed blocks.

    Args:
        text: Prose with the blocks already taken out.

    Returns:
        The same prose with runs of blank lines reduced to one.
    """
    return re.sub(r"\n{3,}", "\n\n", text).strip()