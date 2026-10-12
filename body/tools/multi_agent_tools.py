"""Body/Tools — Multi-Agent Orchestrator tools.

These tools expose the Multi-Agent Orchestrator's public API: deploy
specialist agent teams for research, writing, analysis, planning, editing,
translation, summarization, fact-checking, brainstorming, and scheduling.
"""

from __future__ import annotations

from typing import Any, Dict

from body.tools.tool_spec import ToolRefused, ToolSpec, ToolTarget

__all__ = ["MULTI_AGENT_TOOLS"]


def _require_orchestrator(ctx: Any, tool_name: str) -> Any:
    """Return the MultiAgentOrchestrator instance or refuse."""
    orchestrator = getattr(ctx, "multi_agent_orchestrator", None)
    if orchestrator is None:
        raise ToolRefused(f"{tool_name}: Multi-Agent Orchestrator is not attached")
    return orchestrator


def _make_agent_tool_spec(
    name: str,
    summary: str,
    required_params: tuple,
) -> ToolSpec:
    """Factory to create a multi-agent tool spec with standard resolve/execute."""

    async def resolve(params: Dict[str, Any], ctx: Any) -> ToolTarget:
        _require_orchestrator(ctx, name)
        return ToolTarget(
            category="deploy_agent_team",
            target=f"agent:{name}",
            preview={"action": f"Deploy {name.replace('agent_', '')} agent", "params": {k: params.get(k) for k in required_params}},
            detail=params,
        )

    async def execute(decision: Any, params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
        orchestrator = _require_orchestrator(ctx, name)
        role = name.replace("agent_", "")
        result = await orchestrator.execute_complex_task(
            prompt=f"{summary}: {params.get(required_params[0], '')}",
            context={role: {k: params.get(k) for k in required_params}},
        )
        return result.to_dict()

    return ToolSpec(
        name=name,
        summary=summary,
        resolve=resolve,
        execute=execute,
        returns="aggregated agent result",
    )


MULTI_AGENT_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="agent_research",
        summary="Deploy a Research Specialist agent to find accurate information on a topic",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:research",
            preview={"action": "Research topic", "topic": params.get("topic", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "researcher", params),
        returns="research findings with sources",
    ),
    ToolSpec(
        name="agent_write",
        summary="Deploy a Content Writer agent to produce clear, engaging text",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:write",
            preview={"action": "Write content", "topic": params.get("topic", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "writer", params),
        returns="written content",
    ),
    ToolSpec(
        name="agent_analyze",
        summary="Deploy a Data Analyst agent to interpret data and explain findings",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:analyze",
            preview={"action": "Analyze data", "question": params.get("question", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "analyst", params),
        returns="analysis with key insights",
    ),
    ToolSpec(
        name="agent_plan",
        summary="Deploy a Strategic Planner agent to design a roadmap or execution plan",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:plan",
            preview={"action": "Create plan", "goal": params.get("goal", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "planner", params),
        returns="strategic plan with steps and timeline",
    ),
    ToolSpec(
        name="agent_edit",
        summary="Deploy a Content Editor agent to refine text for clarity and tone",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:edit",
            preview={"action": "Edit text", "instructions": params.get("instructions", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "editor", params),
        returns="edited content with changes",
    ),
    ToolSpec(
        name="agent_translate",
        summary="Deploy a Translator agent to translate text between languages",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:translate",
            preview={"action": "Translate text", "target_lang": params.get("target_lang", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "translator", params),
        returns="translated content",
    ),
    ToolSpec(
        name="agent_summarize",
        summary="Deploy a Summarizer agent to distill long content into a concise summary",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:summarize",
            preview={"action": "Summarize content", "max_length": params.get("max_length", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "summarizer", params),
        returns="summary with key points",
    ),
    ToolSpec(
        name="agent_factcheck",
        summary="Deploy a Fact Checker agent to verify a claim against available evidence",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:factcheck",
            preview={"action": "Fact-check claim", "claim": params.get("claim", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "fact_checker", params),
        returns="verification with confidence score",
    ),
    ToolSpec(
        name="agent_brainstorm",
        summary="Deploy a Brainstormer agent to generate creative ideas on a topic",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:brainstorm",
            preview={"action": "Brainstorm ideas", "topic": params.get("topic", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "brainstormer", params),
        returns="list of ideas with categories",
    ),
    ToolSpec(
        name="agent_schedule_tasks",
        summary="Deploy a Scheduler agent to plan tasks and deadlines",
        resolve=lambda params, ctx: ToolTarget(
            category="deploy_agent_team",
            target="agent:schedule_tasks",
            preview={"action": "Create schedule", "task": params.get("task", "")},
            detail=params,
        ),
        execute=lambda decision, params, ctx: _execute_agent_task(ctx, "scheduler", params),
        returns="schedule with timeline and events",
    ),
]


async def _execute_agent_task(ctx: Any, role: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single agent task via the orchestrator."""
    orchestrator = _require_orchestrator(ctx, f"agent_{role}")
    
    # Map role to the key parameter
    param_map = {
        "researcher": "topic",
        "writer": "topic",
        "analyst": "data",
        "planner": "goal",
        "editor": "text",
        "translator": "text",
        "summarizer": "text",
        "fact_checker": "claim",
        "brainstormer": "topic",
        "scheduler": "task",
    }
    key = param_map.get(role, "topic")
    prompt = f"{role.replace('_', ' ').title()}: {params.get(key, '')}"
    
    result = await orchestrator.execute_complex_task(
        prompt=prompt,
        context={role: params},
    )
    return result.to_dict()