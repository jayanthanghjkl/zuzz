"""Background Agents - agent manager orchestrator."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from brain.background_agents.agent_registry import Agent, AgentRegistry, AgentStatus
from brain.background_agents.colab_bridge import ColabBridge
from brain.background_agents.cron_scheduler import CronScheduler
from brain.background_agents.deployment_handler import DeploymentHandler
from brain.background_agents.exceptions import (
    AgentNotFoundError,
    BackgroundAgentError,
    DeploymentError,
    SchedulingError,
)
from brain.background_agents.github_actions import GitHubActionsKeepAlive
from brain.background_agents.sync_handler import SyncHandler

logger = logging.getLogger(__name__)


class AgentManager:
    """Orchestrate background cloud agents."""

    def __init__(self) -> None:
        self._registry = AgentRegistry()
        self._deployment = DeploymentHandler()
        self._sync = SyncHandler()
        self._scheduler = CronScheduler()
        self._keepalive = GitHubActionsKeepAlive()
        self._bridge = ColabBridge()
        self._deployment._bridge = self._bridge

    async def deploy_agent(self, agent_config: Dict[str, Any]) -> str:
        """Deploy a new agent.
        
        Args:
            agent_config: Agent configuration.
        
        Returns:
            The agent ID.
        
        Raises:
            DeploymentError: On deployment failure.
        """
        agent_id = agent_config.get("agent_id", f"agent_{int(time.time())}")
        agent = Agent(
            agent_id=agent_id,
            name=agent_config.get("name", "Unnamed Agent"),
            agent_type=agent_config.get("agent_type", "generic"),
            config=agent_config,
        )
        self._registry.register(agent)
        try:
            result = await self._deployment.deploy(agent_config)
            agent.colab_url = result.get("colab_url")
            self._registry.update_status(agent_id, AgentStatus.RUNNING)
            logger.info("deployed agent %s", agent_id)
            return agent_id
        except Exception as exc:
            self._registry.update_status(agent_id, AgentStatus.FAILED)
            raise DeploymentError(f"failed to deploy agent {agent_id}: {exc}") from exc

    async def stop_agent(self, agent_id: str) -> Dict[str, Any]:
        """Stop a running agent.
        
        Args:
            agent_id: The agent to stop.
        
        Returns:
            Result dict.
        """
        await self._deployment.stop(agent_id)
        self._scheduler.remove_job(agent_id)
        self._registry.update_status(agent_id, AgentStatus.PAUSED)
        return {"agent_id": agent_id, "status": "stopped"}

    def list_agents(self) -> List[Agent]:
        """Return all registered agents."""
        return self._registry.list_agents()

    def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of an agent."""
        agent = self._registry.get(agent_id)
        if not agent:
            return None
        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "status": agent.status.value,
            "agent_type": agent.agent_type,
            "last_run": agent.last_run,
            "schedule": agent.schedule,
            "colab_url": agent.colab_url,
        }

    async def sync_agent_results(self, agent_id: str) -> Dict[str, Any]:
        """Sync results for an agent."""
        result = await self._sync.sync_agent_results(agent_id)
        self._registry.set_result(agent_id, result)
        return result

    async def schedule_agent(self, agent_id: str, cron_expression: str) -> Dict[str, Any]:
        """Schedule an agent to run on a cron.
        
        Args:
            agent_id: The agent to schedule.
            cron_expression: Cron expression.
        
        Returns:
            Schedule result.
        
        Raises:
            SchedulingError: On scheduling failure.
        """
        agent = self._registry.get(agent_id)
        if not agent:
            raise SchedulingError(f"agent {agent_id} not found")
        self._scheduler.add_job(agent_id, cron_expression)
        agent.schedule = cron_expression
        return {"agent_id": agent_id, "schedule": cron_expression, "next_run": str(self._scheduler.get_next_run(agent_id))}

    async def tick(self) -> None:
        """Run periodic scheduler tick."""
        due = self._scheduler.get_due_jobs()
        for agent_id in due:
            logger.info("scheduled run due for %s", agent_id)
            self._scheduler.mark_run(agent_id)
            self._registry.update_status(agent_id, AgentStatus.RUNNING)
