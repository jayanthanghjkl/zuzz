"""Face API — background agent routes.

Endpoints:
- POST /api/agents/deploy
- GET /api/agents/list
- POST /api/agents/stop
- GET /api/agents/{agent_id}/status
- GET /api/agents/{agent_id}/results
- POST /api/agents/schedule
- POST /api/agents/sync
- POST /api/agents/custom
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


def _manager(request: Request) -> Any:
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="agent manager not attached")
    return manager


class DeployRequest(BaseModel):
    agent_id: Optional[str] = Field(None, description="Custom agent ID")
    name: str = Field(..., description="Agent name")
    agent_type: str = Field(..., description="Agent type")
    config: Dict[str, Any] = Field(default_factory=dict, description="Agent configuration")


class ScheduleRequest(BaseModel):
    cron_expression: str = Field(..., description="Cron expression, e.g. */15 * * * *")


class CustomAgentRequest(BaseModel):
    name: str = Field(..., description="Agent name")
    agent_type: str = Field("custom", description="Agent type")
    code: str = Field(..., description="Agent code")
    config: Dict[str, Any] = Field(default_factory=dict, description="Agent configuration")


@router.post("/api/agents/deploy")
async def deploy_agent(request: Request, body: DeployRequest) -> Dict[str, Any]:
    """Deploy a new background agent."""
    manager = _manager(request)
    config = {"name": body.name, "agent_type": body.agent_type, **body.config}
    if body.agent_id:
        config["agent_id"] = body.agent_id
    try:
        agent_id = await manager.deploy_agent(config)
        return {"agent_id": agent_id, "status": "deployed"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/agents/list")
async def list_agents(request: Request) -> Dict[str, Any]:
    """List all deployed agents."""
    manager = _manager(request)
    agents = manager.list_agents()
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "status": a.status.value,
                "agent_type": a.agent_type,
                "last_run": a.last_run,
            }
            for a in agents
        ]
    }


@router.post("/api/agents/stop")
async def stop_agent(request: Request, agent_id: str) -> Dict[str, Any]:
    """Stop a running agent."""
    manager = _manager(request)
    try:
        return await manager.stop_agent(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/agents/{agent_id}/status")
async def agent_status(request: Request, agent_id: str) -> Dict[str, Any]:
    """Get agent status."""
    manager = _manager(request)
    status = manager.get_agent_status(agent_id)
    if status is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return status


@router.get("/api/agents/{agent_id}/results")
async def agent_results(request: Request, agent_id: str) -> Dict[str, Any]:
    """Get latest results for an agent."""
    manager = _manager(request)
    agent = manager._registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"agent_id": agent_id, "result": agent.result}


@router.post("/api/agents/schedule")
async def schedule_agent(request: Request, agent_id: str, body: ScheduleRequest) -> Dict[str, Any]:
    """Schedule an agent to run on a cron."""
    manager = _manager(request)
    try:
        return await manager.schedule_agent(agent_id, body.cron_expression)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agents/sync")
async def sync_agent(request: Request, agent_id: str) -> Dict[str, Any]:
    """Sync results for an agent."""
    manager = _manager(request)
    try:
        return await manager.sync_agent_results(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agents/custom")
async def create_custom_agent(request: Request, body: CustomAgentRequest) -> Dict[str, Any]:
    """Create a custom agent from code."""
    manager = _manager(request)
    config = {
        "name": body.name,
        "agent_type": body.agent_type,
        "code": body.code,
        **body.config,
    }
    try:
        agent_id = await manager.deploy_agent(config)
        return {"agent_id": agent_id, "status": "deployed"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
