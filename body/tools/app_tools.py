"""Body/Tools — applications and processes.

Opening and closing applications, reached from a chat request.

The interesting part is :meth:`_resolve_open_app`, which resolves the model's
name into an executable **before** the gate is consulted, via
:func:`~body.hands.action_classifier.resolve_target`. Two things follow from
that ordering, and both are the reason it is not done afterwards:

* the gate decides about ``chrome.exe`` rather than about the string ``chrome``,
  so an alias cannot be re-pointed at something else between the decision and
  the launch;
* a name that is not on the whitelist maps to ``run_safe_commands``, which asks
  at every trust level, so an unknown application is a question rather than an
  auto-approval.

:func:`~body.hands.app_registry.resolve_app` can also refuse outright — a
never-launch command is one of those. That refusal happens before any decision
exists, which is the correct order: the thing was never going to be launched
under any trust level. It surfaces as a
:class:`~body.tools.tool_spec.ToolRefused` and is recorded as a precheck
refusal.
"""

from __future__ import annotations

from typing import Any, Dict

from body.hands import action_classifier
from body.hands.exceptions import HandsRefused
from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)


def _resolve_open_app(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve an application name into the executable the gate will judge."""
    name = required_text(params, "app_name", "open_app")
    try:
        resolved = action_classifier.resolve_target("app", name)
    except HandsRefused as exc:
        raise ToolRefused(str(exc)) from exc
    except ValueError as exc:
        raise ToolRefused(str(exc)) from exc
    preview = {
        "description": f"Open {resolved['executable']}",
        "executable": resolved["executable"],
        "whitelisted": bool(resolved.get("whitelisted")),
    }
    if resolved.get("reason"):
        preview["note"] = str(resolved["reason"])
    if not resolved.get("whitelisted"):
        preview["warning"] = (
            "this application is not on the auto-approve list, so it asks "
            "at every trust level"
        )
    return ToolTarget(
        category=str(resolved["category"]),
        target=str(resolved["executable"]),
        preview=preview,
        detail={"app_name": name},
    )


def _execute_open_app(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Launch through the Hands facade with an allowed decision."""
    return context.hands.open_app(decision, str(params["app_name"]))


def _resolve_close_app(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a process or window name into the category that always asks.

    ``close_apps`` asks at every trust level. Closing can lose unsaved work, and
    no trust level is one at which ZUZZ ends an application without being asked.
    """
    name = required_text(params, "process_name", "close_app")
    return ToolTarget(
        category=action_classifier.app_close_category(),
        target=name,
        preview={
            "description": f"Close {name}",
            "warning": "unsaved work in that window may be lost",
        },
    )


def _execute_close_app(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Close through the Hands facade with an allowed decision."""
    return context.hands.close_app(decision, str(params["process_name"]))


#: The application tools.
APP_TOOLS: tuple = (
    ToolSpec(
        name="open_app",
        summary="Launch a desktop application",
        resolve=_resolve_open_app,
        execute=_execute_open_app,
        returns="Which executable was launched, and its process id",
    ),
    ToolSpec(
        name="close_app",
        summary="Ask a running application to close",
        resolve=_resolve_close_app,
        execute=_execute_close_app,
        returns="Whether the application was asked to close",
    ),
)
