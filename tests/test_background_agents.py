"""Tests for background agents."""

from __future__ import annotations

import pytest

from brain.background_agents.agent_registry import Agent, AgentRegistry, AgentStatus
from brain.background_agents.agent_manager import AgentManager
from brain.background_agents.exceptions import AgentNotFoundError, DeploymentError, SchedulingError
from brain.background_agents.cron_scheduler import CronScheduler


def test_agent_registry_register_and_get():
    """Register and retrieve an agent."""
    registry = AgentRegistry()
    agent = Agent(agent_id="a1", name="Test", agent_type="generic")
    registry.register(agent)
    assert registry.get("a1") is agent


def test_agent_registry_list():
    """List all registered agents."""
    registry = AgentRegistry()
    registry.register(Agent(agent_id="a1", name="A", agent_type="generic"))
    registry.register(Agent(agent_id="a2", name="B", agent_type="email_monitor"))
    names = [a.name for a in registry.list_agents()]
    assert "A" in names
    assert "B" in names


def test_agent_registry_update_status():
    """Update agent status."""
    registry = AgentRegistry()
    agent = Agent(agent_id="a1", name="Test", agent_type="generic")
    registry.register(agent)
    registry.update_status("a1", AgentStatus.RUNNING)
    assert agent.status == AgentStatus.RUNNING
    assert agent.last_run is not None


@pytest.mark.asyncio
async def test_agent_manager_deploy():
    """Agent manager can deploy an agent."""
    manager = AgentManager()
    agent_id = await manager.deploy_agent({"name": "Test", "agent_type": "generic"})
    assert agent_id is not None
    assert manager.get_agent_status(agent_id) is not None


@pytest.mark.asyncio
async def test_agent_manager_stop():
    """Agent manager can stop an agent."""
    manager = AgentManager()
    agent_id = await manager.deploy_agent({"name": "Test", "agent_type": "generic"})
    result = await manager.stop_agent(agent_id)
    assert result["status"] == "stopped"


def test_agent_manager_list():
    """Agent manager lists agents."""
    manager = AgentManager()
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        manager.deploy_agent({"name": "A", "agent_type": "generic"})
    )
    agents = manager.list_agents()
    assert any(a.name == "A" for a in agents)


@pytest.mark.asyncio
async def test_agent_manager_schedule():
    """Agent manager can schedule an agent."""
    manager = AgentManager()
    agent_id = await manager.deploy_agent({"name": "Test", "agent_type": "generic"})
    result = await manager.schedule_agent(agent_id, "*/15 * * * *")
    assert result["schedule"] == "*/15 * * * *"


def test_agent_manager_schedule_missing():
    """Scheduling a missing agent raises."""
    manager = AgentManager()
    with pytest.raises(SchedulingError):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            manager.schedule_agent("nonexistent", "*/15 * * * *")
        )


def test_cron_scheduler_add_and_due():
    """Cron scheduler tracks jobs and reports due ones."""
    scheduler = CronScheduler()
    scheduler.add_job("a1", "* * * * *")
    assert scheduler.get_next_run("a1") is not None
    due = scheduler.get_due_jobs()
    assert len(due) == 0
    # Force due by setting next_run to past
    from datetime import datetime, timedelta
    for job in scheduler._jobs.values():
        job["next_run"] = datetime.now() - timedelta(seconds=1)
    assert "a1" in scheduler.get_due_jobs()


def test_cron_scheduler_remove():
    """Cron scheduler removes jobs."""
    scheduler = CronScheduler()
    scheduler.add_job("a1", "* * * * *")
    scheduler.remove_job("a1")
    assert scheduler.get_next_run("a1") is None
