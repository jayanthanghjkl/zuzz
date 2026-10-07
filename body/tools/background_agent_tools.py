"""Body/Tools — background agent tools.

Expose agent_deploy, agent_stop, agent_list, agent_schedule, agent_get_results,
agent_create_custom, agent_sync_now through the action loop.
"""

from __future__ import annotations

from typing import Any, Dict

from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget


def _resolve_agent_deploy(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    agent_type = params.get("agent_type", "")
    if not agent_type:
        raise ToolRefused("agent_deploy: agent_type is empty")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:" + agent_type,
        preview={"agent_type": agent_type},
    )


def _execute_agent_deploy(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    import asyncio
    config = {"agent_type": params["agent_type"], "name": params.get("name", params["agent_type"])}
    agent_id = asyncio.get_event_loop().run_until_complete(manager.deploy_agent(config))
    return {"agent_id": agent_id}


def _resolve_agent_stop(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    agent_id = params.get("agent_id", "")
    if not agent_id:
        raise ToolRefused("agent_stop: agent_id is empty")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:" + agent_id,
        preview={"agent_id": agent_id},
    )


def _execute_agent_stop(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(manager.stop_agent(params["agent_id"]))


def _resolve_agent_list(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent_list",
        preview={},
    )


def _execute_agent_list(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    return {"agents": [a.agent_id for a in manager.list_agents()]}


def _resolve_agent_schedule(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    agent_id = params.get("agent_id", "")
    cron = params.get("cron_expression", "")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:" + agent_id,
        preview={"cron": cron},
    )


def _execute_agent_schedule(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        manager.schedule_agent(params["agent_id"], params["cron_expression"])
    )


def _resolve_agent_get_results(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    agent_id = params.get("agent_id", "")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:" + agent_id,
        preview={"agent_id": agent_id},
    )


def _execute_agent_get_results(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    agent = manager._registry.get(params["agent_id"])
    if not agent:
        return {"error": "agent not found"}
    return {"result": agent.result}


def _resolve_agent_create_custom(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    name = params.get("name", "")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:custom:" + name,
        preview={"name": name},
    )


def _execute_agent_create_custom(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    import asyncio
    config = {"name": params["name"], "agent_type": "custom", "code": params.get("code", "")}
    agent_id = asyncio.get_event_loop().run_until_complete(manager.deploy_agent(config))
    return {"agent_id": agent_id}


def _resolve_agent_sync_now(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    agent_id = params.get("agent_id", "")
    return ToolTarget(
        category=str(ActionCategory.DEPLOY_AGENTS),
        target="agent:" + agent_id,
        preview={"agent_id": agent_id},
    )


def _execute_agent_sync_now(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    manager = getattr(context, "agent_manager", None)
    if manager is None:
        return {"error": "agent manager not attached"}
    import asyncio
    return asyncio.get_event_loop().run_until_complete(manager.sync_agent_results(params["agent_id"]))


BACKGROUND_AGENT_TOOLS: tuple = (
    ToolSpec(
        name="agent_deploy",
        summary="Deploy a background cloud agent",
        resolve=_resolve_agent_deploy,
        execute=_execute_agent_deploy,
        returns="agent_id",
    ),
    ToolSpec(
        name="agent_stop",
        summary="Stop a background agent",
        resolve=_resolve_agent_stop,
        execute=_execute_agent_stop,
        returns="stop result",
    ),
    ToolSpec(
        name="agent_list",
        summary="List deployed background agents",
        resolve=_resolve_agent_list,
        execute=_execute_agent_list,
        returns="agent list",
    ),
    ToolSpec(
        name="agent_schedule",
        summary="Schedule a background agent",
        resolve=_resolve_agent_schedule,
        execute=_execute_agent_schedule,
        returns="schedule result",
    ),
    ToolSpec(
        name="agent_get_results",
        summary="Get latest agent results",
        resolve=_resolve_agent_get_results,
        execute=_execute_agent_get_results,
        returns="agent results",
    ),
    ToolSpec(
        name="agent_create_custom",
        summary="Create a custom background agent",
        resolve=_resolve_agent_create_custom,
        execute=_execute_agent_create_custom,
        returns="agent_id",
    ),
    ToolSpec(
        name="agent_sync_now",
        summary="Sync agent results immediately",
        resolve=_resolve_agent_sync_now,
        execute=_execute_agent_sync_now,
        returns="sync result",
    ),
)
