"""Body/Tools — the shapes a tool call resolves into.

A tool has two halves and this module names both, because keeping them apart is
what stops a tool from deciding anything.

* :class:`ToolTarget` is what :meth:`ToolSpec.resolve` returns: the concrete
  thing the tool will act on, and the trust category the gate must judge. It is
  produced **before** the gate sees the action, exactly as the desktop routes
  resolve ``chrome`` to ``chrome.exe`` first — if the gate judged the alias and
  the Body acted on the executable, an alias could smuggle in something else.
* :meth:`ToolSpec.execute` is the Body call itself, and takes the decision
  first like every other Body method.

Neither can allow or refuse. A refusal raised from here is a *description* of a
target that does not exist — an unknown application, a path that climbs out of
the project — and it happens before any decision exists, which is why it is
recorded as a precheck refusal in :mod:`face.api.chat_actions` rather than as a
gate verdict.

:mod:`body.tools.registry` is the one place a name is bound to a
implementation, and the one place the catalogue is assembled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = ["ToolContext", "ToolRefused", "ToolSpec", "ToolTarget", "required_text"]


class ToolRefused(ValueError):
    """A tool call names a target that does not exist or cannot be used.

    Raised during resolution, so it is a refusal that happened before the gate
    was consulted. It is deliberately distinct from the Body's own refusal
    classes: those say "you may not do this", this says "there is nothing here
    to do it to".
    """


@dataclass(frozen=True)
class ToolContext:
    """The collaborators a tool may act through.

    Held rather than imported, so a tool cannot reach anything the caller did
    not hand it. That is what lets the tests substitute a recording actuator and
    a temporary directory and know that no test can touch the real desktop or
    the developer's files.

    Attributes:
        hands: The :class:`~body.hands.hands.Hands` facade.
        files: The :class:`~body.fingers.file_manager.FileManager`.
        directories: The :class:`~body.fingers.directory_manager.DirectoryManager`.
        hypothalamus: The resource layer, for machine telemetry.
        occipital_lobe: The :class:`~brain.occipital_lobe.occipital_lobe.OccipitalLobe`,
            for the vision tools. ``None`` when the Lobe is not attached, which the
            vision tools refuse on rather than guessing at a capture.
        browser: The :class:`~body.legs.browser_actions.BrowserActions`, for the
            browser tools. ``None`` when the Legs layer is not attached.
        write_decision: An *additional* decision the caller already holds, used by
            ``browser_download`` — a download is both a ``download_files`` and a
            ``write_files`` act, and only one decision is ever passed as the
            primary one. Left to the route, because minting a second decision here
            would be the Body allowing itself.
    """

    hands: Any
    files: Any
    directories: Any
    hypothalamus: Any = None
    occipital_lobe: Any = None
    browser: Any = None
    write_decision: Any = None


@dataclass(frozen=True)
class ToolTarget:
    """What a tool call resolves to, ready for the gate.

    Attributes:
        category: The trust category to decide, from
            ``config/trust_levels.json``.
        target: The concrete thing acted on — an executable, a ``"100,200"``
            coordinate, an absolute path.
        preview: What a confirmation prompt should show the user.
        detail: Extra facts recorded alongside the action.
    """

    category: str
    target: str
    preview: Dict[str, Any] = field(default_factory=dict)
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    """One tool the model may call.

    Attributes:
        name: The tool name, matching ``brain.cerebrum.tools_schema``.
        summary: One line in plain language, used in logs and responses.
        resolve: ``(params, context) -> ToolTarget``, run before the gate.
        execute: ``(decision, params, context) -> Any``, run with an allowed
            decision and never without one.
        returns: Short description of what the tool produces, for the response.
    """

    name: str
    summary: str
    resolve: Callable[[Dict[str, Any], ToolContext], ToolTarget]
    execute: Callable[[Any, Dict[str, Any], ToolContext], Any]
    returns: str = ""

    def describe(self) -> Dict[str, str]:
        """Return the spec as plain data for a response or a log line."""
        return {"tool": self.name, "summary": self.summary, "returns": self.returns}


def required_text(params: Dict[str, Any], key: str, tool: str) -> str:
    """Return a required string parameter, refusing a blank one.

    Args:
        params: The validated parameters.
        key: The parameter name.
        tool: The tool name, for the error message.

    Returns:
        The stripped value.

    Raises:
        ToolRefused: If the parameter is missing, not a string, or blank. A
            blank application name or path is a request to act on nothing, and
            the layers below would each resolve it to their own surprising
            answer.
    """
    value: Optional[Any] = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolRefused(f"{tool}: {key!r} is empty, so there is nothing to act on")
    return value.strip()


def bounded_text(params: Dict[str, Any], key: str, tool: str, limit: int = 80) -> str:
    """Return a string parameter truncated for use as a decision target.

    The Amygdala scores a target by pattern-matching it, so the target has to
    carry enough of the value to be meaningful. A whole file's contents would
    both swamp that and put the user's data into every threat log, so a long
    value is cut to its first ``limit`` characters.

    Args:
        params: The validated parameters.
        key: The parameter name.
        tool: The tool name, for the error message.
        limit: How much of the value the target keeps.

    Returns:
        The stripped value, truncated to ``limit`` characters.

    Raises:
        ToolRefused: If the parameter is missing or not a string.
    """
    value: Optional[Any] = params.get(key)
    if not isinstance(value, str):
        raise ToolRefused(f"{tool}: {key!r} must be a string")
    return value.strip()[:limit]


def key_names(params: Dict[str, Any], tool: str) -> List[str]:
    """Return a validated list of key names.

    Args:
        params: The validated parameters.
        tool: The tool name, for the error message.

    Returns:
        The key names in the order given.

    Raises:
        ToolRefused: If ``keys`` is missing or empty. An empty combination is
            not a shortcut for "do nothing" — it would be pressed as a
            combination, which is a different act.
    """
    value = params.get("keys")
    if not isinstance(value, list) or not value:
        raise ToolRefused(f"{tool}: name at least one key to press")
    return [str(item).strip() for item in value if str(item).strip()]
