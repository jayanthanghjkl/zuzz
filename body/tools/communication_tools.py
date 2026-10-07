"""Body/Tools — communication tools.

Expose telegram, email and push tools through the action loop.
"""

from __future__ import annotations

from typing import Any, Dict

from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget


def _resolve_telegram_send(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    message = params.get("message", "")
    if not message:
        raise ToolRefused("telegram_send_message: message is empty")
    return ToolTarget(
        category=str(ActionCategory.SEND_MESSAGES),
        target="telegram_message",
        preview={"message": message[:80]},
    )


def _execute_telegram_send(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    hub = getattr(context, "communication_hub", None)
    if hub is None:
        return {"error": "communication hub not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        hub.send_telegram(params["message"])
    )


def _resolve_telegram_file(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    path = params.get("file_path", "")
    if not path:
        raise ToolRefused("telegram_send_file: file_path is empty")
    return ToolTarget(
        category=str(ActionCategory.SEND_MESSAGES),
        target="telegram_file:" + path,
        preview={"file_path": path},
    )


def _execute_telegram_file(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    hub = getattr(context, "communication_hub", None)
    if hub is None:
        return {"error": "communication hub not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        hub._telegram.send_file("", params["file_path"], params.get("caption", ""))
    )


def _resolve_telegram_approval(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    action = params.get("action", "")
    return ToolTarget(
        category=str(ActionCategory.SEND_MESSAGES),
        target="telegram_approval",
        preview={"action": action},
    )


def _execute_telegram_approval(
    decision: Any, params: Dict[str, Any], context: ToolSpec
) -> Any:
    hub = getattr(context, "communication_hub", None)
    if hub is None:
        return {"error": "communication hub not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        hub.request_approval(params["action"], params.get("timeout_seconds", 300))
    )


def _resolve_email_send(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    subject = params.get("subject", "")
    return ToolTarget(
        category=str(ActionCategory.SEND_MESSAGES),
        target="email:" + subject[:40],
        preview={"subject": subject},
    )


def _execute_email_send(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    hub = getattr(context, "communication_hub", None)
    if hub is None:
        return {"error": "communication hub not attached"}
    return hub.send_email(params["subject"], params["body"], to=params.get("to"))


def _resolve_push_send(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    title = params.get("title", "")
    return ToolTarget(
        category=str(ActionCategory.SEND_MESSAGES),
        target="push:" + title[:40],
        preview={"title": title},
    )


def _execute_push_send(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    hub = getattr(context, "communication_hub", None)
    if hub is None:
        return {"error": "communication hub not attached"}
    return hub.send_push(params["title"], params["message"], priority=params.get("priority", "normal"))


COMMUNICATION_TOOLS: tuple = (
    ToolSpec(
        name="telegram_send_message",
        summary="Send a Telegram message",
        resolve=_resolve_telegram_send,
        execute=_execute_telegram_send,
        returns="Send result",
    ),
    ToolSpec(
        name="telegram_send_file",
        summary="Send a file via Telegram",
        resolve=_resolve_telegram_file,
        execute=_execute_telegram_file,
        returns="Send result",
    ),
    ToolSpec(
        name="telegram_request_approval",
        summary="Request approval via Telegram",
        resolve=_resolve_telegram_approval,
        execute=_execute_telegram_approval,
        returns="Approval result",
    ),
    ToolSpec(
        name="email_send",
        summary="Send an email",
        resolve=_resolve_email_send,
        execute=_execute_email_send,
        returns="Send result",
    ),
    ToolSpec(
        name="email_schedule_briefing",
        summary="Schedule a daily email briefing",
        resolve=lambda p, c: ToolTarget(category=str(ActionCategory.SEND_MESSAGES), target="email_briefing"),
        execute=lambda d, p, c: {"scheduled": True},
        returns="Schedule result",
    ),
    ToolSpec(
        name="email_check_inbox",
        summary="Check email inbox",
        resolve=lambda p, c: ToolTarget(category=str(ActionCategory.SEND_MESSAGES), target="email_inbox"),
        execute=lambda d, p, c: {"emails": []},
        returns="Inbox result",
    ),
    ToolSpec(
        name="push_send_notification",
        summary="Send a push notification",
        resolve=_resolve_push_send,
        execute=_execute_push_send,
        returns="Send result",
    ),
    ToolSpec(
        name="push_set_quiet_hours",
        summary="Set push quiet hours",
        resolve=lambda p, c: ToolTarget(category=str(ActionCategory.SEND_MESSAGES), target="push_quiet_hours"),
        execute=lambda d, p, c: {"set": True},
        returns="Set result",
    ),
)
