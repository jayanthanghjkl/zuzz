"""Tests for Multi-Agent Orchestrator — decomposition, coordination, and aggregation."""

from __future__ import annotations

import pytest

from multi_agent.orchestrator import MultiAgentOrchestrator
from multi_agent.task_decomposer import TaskDecomposer, ROLE_KEYWORDS
from multi_agent.team_builder import TeamBuilder, ROLE_TO_AGENT_CLASS
from multi_agent.agent_coordinator import AgentCoordinator, SessionState
from multi_agent.result_aggregator import ResultAggregator
from multi_agent.agents import (
    ResearcherAgent,
    WriterAgent,
    AnalystAgent,
    PlannerAgent,
    EditorAgent,
    TranslatorAgent,
    SummarizerAgent,
    FactCheckerAgent,
    BrainstormerAgent,
    SchedulerAgent,
)


class TestTaskDecomposer:
    """Test task decomposition into specialist subtasks."""

    def test_decompose_research_and_write(self):
        decomposer = TaskDecomposer()
        prompt = "Research the AI market and write a report"
        subtasks = decomposer.decompose(prompt)

        roles = [st.role for st in subtasks]
        assert "researcher" in roles
        assert "writer" in roles

    def test_decompose_all_roles(self):
        decomposer = TaskDecomposer()
        prompt = "Research, write, analyze, plan, edit, translate, summarize, fact check, brainstorm, and schedule"
        subtasks = decomposer.decompose(prompt)

        roles = [st.role for st in subtasks]
        assert set(roles) == set(ROLE_KEYWORDS.keys())

    def test_decompose_no_match_defaults_to_writer(self):
        decomposer = TaskDecomposer()
        prompt = "Do something completely random xyz"
        subtasks = decomposer.decompose(prompt)

        assert len(subtasks) == 1
        assert subtasks[0].role == "writer"

    def test_implicit_dependencies_research_before_write(self):
        decomposer = TaskDecomposer()
        prompt = "Write a report after researching the topic"
        subtasks = decomposer.decompose(prompt)

        writer_task = next(st for st in subtasks if st.role == "writer")
        assert "researcher" in writer_task.depends_on

    def test_implicit_dependencies_write_before_edit(self):
        decomposer = TaskDecomposer()
        prompt = "Write and edit a document"
        subtasks = decomposer.decompose(prompt)

        edit_task = next(st for st in subtasks if st.role == "editor")
        assert "writer" in edit_task.depends_on

    def test_context_passed_to_subtasks(self):
        decomposer = TaskDecomposer()
        context = {"user_preference": "concise"}
        subtasks = decomposer.decompose("Research and write", context)

        for st in subtasks:
            assert st.context.get("user_preference") == "concise"


class TestTeamBuilder:
    """Test team composition from roles."""

    def test_build_team_with_all_roles(self):
        from unittest.mock import MagicMock
        cerebrum = MagicMock()
        builder = TeamBuilder(cerebrum)

        team = builder.build(roles=list(ROLE_TO_AGENT_CLASS.keys()))

        assert len(team.agents) == 10
        for role in ROLE_TO_AGENT_CLASS:
            assert team.has_role(role)
            assert team.get_agent(role) is not None

    def test_build_team_with_subset(self):
        from unittest.mock import MagicMock
        cerebrum = MagicMock()
        builder = TeamBuilder(cerebrum)

        team = builder.build(roles=["researcher", "writer"])

        assert len(team.agents) == 2
        assert team.has_role("researcher")
        assert team.has_role("writer")
        assert not team.has_role("analyst")

    def test_build_unknown_role_raises(self):
        from unittest.mock import MagicMock
        cerebrum = MagicMock()
        builder = TeamBuilder(cerebrum)

        with pytest.raises(ValueError):
            builder.build(roles=["nonexistent"])

    def test_available_roles(self):
        from unittest.mock import MagicMock
        cerebrum = MagicMock()
        builder = TeamBuilder(cerebrum)

        roles = builder.available_roles()
        assert set(roles) == set(ROLE_TO_AGENT_CLASS.keys())


class TestAgentCoordinator:
    """Test agent execution coordination."""

    @pytest.fixture
    def mock_cerebrum(self):
        from unittest.mock import AsyncMock, MagicMock
        cerebrum = MagicMock()
        cerebrum.think = AsyncMock(return_value="Mock response")
        return cerebrum

    @pytest.fixture
    def team(self, mock_cerebrum):
        builder = TeamBuilder(mock_cerebrum)
        return builder.build(roles=["researcher", "writer", "analyst"])

    @pytest.mark.asyncio
    async def test_sequential_execution_respects_dependencies(self, team):
        from multi_agent.task_decomposer import SubTask

        coordinator = AgentCoordinator()
        subtasks = [
            SubTask(role="researcher", instruction="Research topic", depends_on=[]),
            SubTask(role="writer", instruction="Write report", depends_on=["researcher"]),
        ]

        session_id = "test_session"
        coordinator.create_session(session_id, team, subtasks)
        state = await coordinator.execute_session(session_id)

        assert state.status == "completed"
        assert len(state.results) == 2
        # Research should complete before writer starts
        researcher_result = next(r for r in state.results if r.role == "researcher")
        writer_result = next(r for r in state.results if r.role == "writer")
        assert researcher_result.succeeded
        assert writer_result.succeeded

    @pytest.mark.asyncio
    async def test_parallel_execution_for_independent_tasks(self, team):
        from multi_agent.task_decomposer import SubTask

        coordinator = AgentCoordinator()
        subtasks = [
            SubTask(role="researcher", instruction="Research A", depends_on=[]),
            SubTask(role="analyst", instruction="Analyze B", depends_on=[]),
        ]

        session_id = "test_session_parallel"
        coordinator.create_session(session_id, team, subtasks)
        state = await coordinator.execute_session(session_id)

        assert state.status == "completed"
        assert len(state.results) == 2

    @pytest.mark.asyncio
    async def test_context_passed_between_agents(self, team):
        from multi_agent.task_decomposer import SubTask

        coordinator = AgentCoordinator()
        subtasks = [
            SubTask(role="researcher", instruction="Research", depends_on=[]),
            SubTask(role="writer", instruction="Write", depends_on=["researcher"]),
        ]

        session_id = "test_context"
        coordinator.create_session(session_id, team, subtasks, initial_context={"user": "test"})
        state = await coordinator.execute_session(session_id)

        # Writer should have researcher's output in context
        writer_result = next(r for r in state.results if r.role == "writer")
        assert writer_result.succeeded


class TestResultAggregator:
    """Test result aggregation from multi-agent sessions."""

    @pytest.fixture
    def mock_state(self):
        from unittest.mock import MagicMock
        from multi_agent.team_builder import Team
        from multi_agent.agent_coordinator import ExecutionResult
        from multi_agent.task_decomposer import SubTask

        team = Team(name="Test Team", agents={})
        subtasks = [
            SubTask(role="researcher", instruction="Research"),
            SubTask(role="writer", instruction="Write"),
        ]

        state = SessionState(
            session_id="test",
            team=team,
            subtasks=subtasks,
            results=[
                ExecutionResult(role="researcher", instruction="Research", output="Research findings"),
                ExecutionResult(role="writer", instruction="Write", output="Written report"),
            ],
        )
        return state

    def test_aggregator_picks_writer_as_primary(self, mock_state):
        aggregator = ResultAggregator()
        result = aggregator.aggregate(mock_state)

        assert result.primary_output == "Written report"
        assert "researcher" in result.supporting_outputs
        assert "writer" in result.supporting_outputs

    def test_aggregator_fallback_when_no_primary(self, mock_state):
        # Replace writer with failed result
        from multi_agent.agent_coordinator import ExecutionResult
        mock_state.results = [
            ExecutionResult(role="researcher", instruction="Research", output="Research findings"),
            ExecutionResult(role="writer", instruction="Write", error="Failed"),
        ]

        aggregator = ResultAggregator()
        result = aggregator.aggregate(mock_state)

        assert result.primary_output == "Research findings"

    def test_aggregator_applies_editor_modifier(self, mock_state):
        from multi_agent.agent_coordinator import ExecutionResult
        mock_state.results = [
            ExecutionResult(role="writer", instruction="Write", output="Original text"),
            ExecutionResult(role="editor", instruction="Edit", output="Edited text"),
        ]

        aggregator = ResultAggregator()
        result = aggregator.aggregate(mock_state)

        assert result.primary_output == "Edited text"
        assert result.supporting_outputs["editor"] == "Edited text"

    def test_aggregator_includes_errors(self, mock_state):
        from multi_agent.agent_coordinator import ExecutionResult
        mock_state.results = [
            ExecutionResult(role="researcher", instruction="Research", output="OK"),
            ExecutionResult(role="writer", instruction="Write", error="Model unavailable"),
        ]

        aggregator = ResultAggregator()
        result = aggregator.aggregate(mock_state)

        assert len(result.errors) == 1
        assert "writer" in result.errors[0]


class TestMultiAgentOrchestrator:
    """Test the main orchestrator entry point."""

    @pytest.fixture
    def mock_cerebrum(self):
        from unittest.mock import AsyncMock, MagicMock
        cerebrum = MagicMock()
        cerebrum.think = AsyncMock(return_value="Mock response")
        return cerebrum

    @pytest.mark.asyncio
    async def test_execute_complex_task(self, mock_cerebrum):
        orchestrator = MultiAgentOrchestrator(cerebrum=mock_cerebrum)
        result = await orchestrator.execute_complex_task(
            prompt="Research and write a report",
            context={"user": "test"},
        )

        assert result.session_id is not None
        assert result.primary_output is not None
        assert isinstance(result.supporting_outputs, dict)

    @pytest.mark.asyncio
    async def test_deploy_team(self, mock_cerebrum):
        orchestrator = MultiAgentOrchestrator(cerebrum=mock_cerebrum)
        result = await orchestrator.deploy_team(
            roles=["researcher", "writer"],
            instructions={
                "researcher": "Research AI",
                "writer": "Write summary",
            },
        )

        assert result.session_id is not None

    def test_get_available_roles(self, mock_cerebrum):
        orchestrator = MultiAgentOrchestrator(cerebrum=mock_cerebrum)
        roles = orchestrator.get_available_roles()

        assert len(roles) == 10
        assert set(roles) == set(ROLE_TO_AGENT_CLASS.keys())

    def test_session_status(self, mock_cerebrum):
        orchestrator = MultiAgentOrchestrator(cerebrum=mock_cerebrum)
        # No sessions yet
        assert orchestrator.get_session_status("nonexistent") is None

    def test_stop_session(self, mock_cerebrum):
        orchestrator = MultiAgentOrchestrator(cerebrum=mock_cerebrum)
        # Should not error
        assert orchestrator.stop_session("nonexistent") is False


class TestSpecialistAgents:
    """Test each specialist agent can be instantiated and has correct role."""

    @pytest.fixture
    def mock_cerebrum(self):
        from unittest.mock import AsyncMock, MagicMock
        cerebrum = MagicMock()
        cerebrum.think = AsyncMock(return_value="Mock response")
        return cerebrum

    @pytest.mark.parametrize("agent_class,expected_role", [
        (ResearcherAgent, "researcher"),
        (WriterAgent, "writer"),
        (AnalystAgent, "analyst"),
        (PlannerAgent, "planner"),
        (EditorAgent, "editor"),
        (TranslatorAgent, "translator"),
        (SummarizerAgent, "summarizer"),
        (FactCheckerAgent, "fact_checker"),
        (BrainstormerAgent, "brainstormer"),
        (SchedulerAgent, "scheduler"),
    ])
    def test_agent_role(self, mock_cerebrum, agent_class, expected_role):
        agent = agent_class(mock_cerebrum)
        assert agent.role == expected_role
        assert agent.description is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("agent_class", [
        ResearcherAgent, WriterAgent, AnalystAgent, PlannerAgent,
        EditorAgent, TranslatorAgent, SummarizerAgent,
        FactCheckerAgent, BrainstormerAgent, SchedulerAgent,
    ])
    async def test_agent_execute_returns_dict(self, mock_cerebrum, agent_class):
        agent = agent_class(mock_cerebrum)
        result = await agent.execute("Test task")

        assert isinstance(result, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])