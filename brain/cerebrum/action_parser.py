"""Cerebrum — action parsing.

Turns a model's reply back into a list of things ZUZZ should actually do.

The model writes a fenced block per action::

    ```action
    {"tool": "open_app", "params": {"app_name": "chrome"},
     "explanation": "Opening Chrome"}
    ```

Parsing is the untrusted boundary. The text arrives from a language model
answering a user who may have been talked into asking for something else, so
three properties matter more than cleverness:

* **Nothing is executed here.** A :class:`ToolCall` is a request, not a
  permission. The gate decides, in :mod:`face.api.chat_actions`.
* **Every block is accounted for.** A block that will not parse becomes a
  problem the user is told about, not a silent omission — a tool call quietly
  dropped is indistinguishable from ZUZZ pretending it acted.
* **Blocks are always stripped from the prose.** Even a malformed one, so the
  user never sees raw protocol in their chat bubble.

Parameters are validated against the schema here as well as at execution. A
second check costs nothing and means a bad call never reaches the gate, so the
audit log records refusals rather than a stream of nonsense.

The mechanics — reading the JSON out of a fence, coercing and range-checking
the parameters, deciding what counts as an action language — live in
:mod:`brain.cerebrum.parser_validation`. This module is the reply split and the
two shapes it produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from brain.cerebrum.parser_validation import (
    ACTION_LANGUAGES,
    FENCE,
    MAX_ACTIONS_PER_REPLY,
    build_call,
    read_block,
    tidy_prose,
)

__all__ = [
    "MAX_ACTIONS_PER_REPLY",
    "ParsedReply",
    "ToolCall",
    "parse_actions",
    "strip_actions",
]


@dataclass(frozen=True)
class ToolCall:
    """One action the model asked ZUZZ to take.

    Attributes:
        tool: The tool name, exactly as the model wrote it.
        params: Parameters validated against the tool's schema.
        explanation: The model's own one-line reason, shown to the user.
    """

    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return the call as plain data for a response or a log line."""
        return {
            "tool": self.tool,
            "params": dict(self.params),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class ParsedReply:
    """A model reply split into what to say and what to do.

    Attributes:
        prose: The reply with every action block removed.
        calls: The action calls that survived validation.
        problems: Why any block was dropped or could not be used.
        dropped: The same failures, each with the tool name the model used, so a
            caller can report which action failed rather than only that one did.
            An entry with an empty ``tool`` is a block that named no tool at all.
    """

    prose: str
    calls: List[ToolCall] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    dropped: List[Dict[str, str]] = field(default_factory=list)

    @property
    def has_actions(self) -> bool:
        """Return True when at least one action survived validation."""
        return bool(self.calls)

    def to_dict(self) -> Dict[str, Any]:
        """Return the parse as plain data."""
        return {
            "prose": self.prose,
            "calls": [call.to_dict() for call in self.calls],
            "problems": list(self.problems),
            "dropped": [dict(entry) for entry in self.dropped],
        }


def parse_actions(text: str) -> ParsedReply:
    """Split a model reply into prose and action calls.

    Args:
        text: The raw reply, fenced blocks and all.

    Returns:
        A :class:`ParsedReply`. ``calls`` is empty and ``problems`` explains why
        for a reply with no usable action, which is the ordinary case for a
        conversational reply.
    """
    calls: List[ToolCall] = []
    problems: List[str] = []
    dropped: List[Dict[str, str]] = []

    def consume(match: "Any") -> str:
        """Record one block's call and return the text it replaced."""
        if match.group("lang").strip().lower() not in ACTION_LANGUAGES:
            return match.group(0)
        payload, reason = read_block(match.group("body"))
        if payload is None:
            # A block that could not even be decoded is still a block the model
            # meant to be an action, so it is reported rather than skipped.
            problems.append(reason)
            dropped.append({"tool": "", "reason": reason})
            return ""
        call = build_call(payload, problems, dropped)
        if call is not None:
            calls.append(call)
        return ""

    prose = FENCE.sub(consume, str(text or ""))
    if len(calls) > MAX_ACTIONS_PER_REPLY:
        reason = (
            f"the reply asked for {len(calls)} actions; only the first "
            f"{MAX_ACTIONS_PER_REPLY} were considered"
        )
        problems.append(reason)
        dropped.append({"tool": "", "reason": reason})
        calls = calls[:MAX_ACTIONS_PER_REPLY]
    return ParsedReply(
        prose=tidy_prose(prose), calls=calls, problems=problems, dropped=dropped
    )


def strip_actions(text: str) -> str:
    """Return a reply with its action blocks removed.

    The frontend needs this too — tokens arrive one at a time, so the raw JSON
    is already on screen before the server can parse it. Doing it here as well
    keeps the rule in one place: an action block is protocol, never prose.

    Args:
        text: The raw reply.

    Returns:
        The reply as the user should read it.
    """
    return parse_actions(text).prose