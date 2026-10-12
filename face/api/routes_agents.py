"""API routes for Multi-Agent Orchestrator and Mirror Neurons."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from brain.mirror_neurons import MirrorNeurons
from multi_agent.orchestrator import MultiAgentOrchestrator

router = APIRouter(prefix="/api", tags=["agents", "mirror"])

# These will be set by server.py
mirror_neurons: Optional[MirrorNeurons] = None
multi_agent_orchestrator: Optional[MultiAgentOrchestrator] = None


def set_dependencies(mn: MirrorNeurons, mao: MultiAgentOrchestrator) -> None:
    global mirror_neurons, multi_agent_orchestrator
    mirror_neurons = mn
    multi_agent_orchestrator = mao


# --- Mirror Neurons Endpoints ---

class ObservationStartRequest(BaseModel):
    pass


class ObservationStopRequest(BaseModel):
    pass


class CreateMacroRequest(BaseModel):
    pattern_id: str
    name: str


class SuggestRequest(BaseModel):
    threshold: int = 5


@router.post("/mirror/observe/start")
async def mirror_observe_start(request: ObservationStartRequest) -> Dict[str, Any]:
    """Begin observing user actions to detect repeatable patterns."""
    if mirror_neurons is None:
        raise HTTPException(status_code=503, detail="Mirror Neurons not initialized")
    try:
        session_id = mirror_neurons.start_observation()
        return {"session_id": session_id, "status": "observing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mirror/observe/stop")
async def mirror_observe_stop(request: ObservationStopRequest) -> Dict[str, Any]:
    """Stop observing and detect patterns from the captured actions."""
    if mirror_neurons is None:
        raise HTTPException(status_code=503, detail="Mirror Neurons not initialized")
    try:
        session = mirror_neurons.stop_observation()
        return session.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mirror/patterns")
async def mirror_patterns() -> Dict[str, Any]:
    """Get patterns detected from the last observation session."""
    if mirror_neurons is None:
        raise HTTPException(status_code=503, detail="Mirror Neurons not initialized")
    try:
        patterns = mirror_neurons.get_detected_patterns()
        return {"patterns": [p.to_dict() for p in patterns]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mirror/create-macro")
async def mirror_create_macro(request: CreateMacroRequest) -> Dict[str, Any]:
    """Create a disabled macro from a detected pattern."""
    if mirror_neurons is None:
        raise HTTPException(status_code=503, detail="Mirror Neurons not initialized")
    try:
        macro = mirror_neurons.create_macro_from_pattern(request.pattern_id)
        return macro.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mirror/suggest")
async def mirror_suggest(request: SuggestRequest) -> Dict[str, Any]:
    """Suggest automations for patterns meeting the repetition threshold."""
    if mirror_neurons is None:
        raise HTTPException(status_code=503, detail="Mirror Neurons not initialized")
    try:
        macro = mirror_neurons.suggest_automation()
        if macro is None:
            return {"suggestions": []}
        return {"suggestions": [macro.to_dict()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Multi-Agent Orchestrator Endpoints ---

class DeployTeamRequest(BaseModel):
    prompt: str
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class AgentTaskRequest(BaseModel):
    task: str
    context: Optional[Dict[str, Any]] = None


@router.post("/agents/deploy-team")
async def agents_deploy_team(request: DeployTeamRequest) -> Dict[str, Any]:
    """Decompose a complex prompt and deploy a specialist agent team."""
    if multi_agent_orchestrator is None:
        raise HTTPException(status_code=503, detail="Multi-Agent Orchestrator not initialized")
    try:
        result = await multi_agent_orchestrator.execute_complex_task(
            prompt=request.prompt,
            context=request.context,
            session_id=request.session_id,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/available-roles")
async def agents_available_roles() -> Dict[str, Any]:
    """Get list of available specialist roles."""
    if multi_agent_orchestrator is None:
        raise HTTPException(status_code=503, detail="Multi-Agent Orchestrator not initialized")
    try:
        roles = multi_agent_orchestrator.get_available_roles()
        return {"roles": roles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{role}/task")
async def agents_role_task(role: str, request: AgentTaskRequest) -> Dict[str, Any]:
    """Deploy a single specialist agent for a task."""
    if multi_agent_orchestrator is None:
        raise HTTPException(status_code=503, detail="Multi-Agent Orchestrator not initialized")
    available = multi_agent_orchestrator.get_available_roles()
    if role not in available:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role}")
    try:
        result = await multi_agent_orchestrator.deploy_team(
            roles=[role],
            instructions={role: request.task},
            context=request.context,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/sessions")
async def agents_sessions() -> Dict[str, Any]:
    """List all multi-agent sessions."""
    if multi_agent_orchestrator is None:
        raise HTTPException(status_code=503, detail="Multi-Agent Orchestrator not initialized")
    try:
        sessions = multi_agent_orchestrator.list_sessions()
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))