"""Multi-Agent Orchestrator — break a complex prompt into specialist subtasks.

A complex user request like "research the market, write a report, and translate
it to Spanish" is not one task — it is three, each needing a different kind of
thinking. The :class:`TaskDecomposer` splits the prompt, assigns each piece to
a specialist role, and returns a list of :class:`SubTask` objects the orchestrator
can hand to a team.

The decomposition is keyword-driven on purpose: it is fast, it does not need a
model call, and the user can see exactly why a prompt was split the way it was.
The keywords are in :data:`ROLE_KEYWORDS` so they are readable and adjustable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "SubTask",
    "TaskDecomposer",
    "ROLE_KEYWORDS",
]


@dataclass
class SubTask:
    """One piece of a decomposed complex task.

    Attributes:
        role: Which specialist should handle this. One of the keys in
            :data:`ROLE_KEYWORDS`.
        instruction: What the specialist should do, in natural language.
        context: Extra data the specialist may need (previous outputs, files,
            user preferences).
        depends_on: Role names that must finish before this one starts.
            Empty means it can run immediately.
        priority: Lower runs first. Used when dependencies allow parallelism.
    """

    role: str
    instruction: str
    context: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    priority: int = 0


#: Map from specialist role to the keywords that trigger it.
#: The keys are the role names the orchestrator and the UI use.
#: The values are substrings (case-insensitive) that, when present in the
#: user's prompt, cause that role to be included in the team.
ROLE_KEYWORDS: Dict[str, List[str]] = {
    "researcher": ["research", "find", "look up", "search", "investigate", "gather", "sources", "literature"],
    "writer": ["write", "draft", "compose", "create", "author", "content", "article", "report", "document"],
    "analyst": ["analyze", "evaluate", "assess", "compare", "review", "examine", "interpret", "metrics", "data"],
    "planner": ["plan", "roadmap", "strategy", "schedule", "timeline", "milestone", "steps", "approach"],
    "editor": ["edit", "proofread", "refine", "polish", "improve", "grammar", "style", "clarify", "tighten"],
    "translator": ["translate", "localize", "language", "spanish", "french", "german", "chinese", "japanese"],
    "summarizer": ["summarize", "summary", "condense", "digest", "tl;dr", "brief", "key points", "essence"],
    "fact_checker": ["fact check", "verify", "validate", "confirm", "accuracy", "true", "false", "claim"],
    "brainstormer": ["brainstorm", "ideate", "ideas", "suggest", "creative", "innovate", "concepts", "possibilities"],
    "scheduler": ["schedule", "calendar", "meeting", "appointment", "booking", "time slot", "availability"],
}


class TaskDecomposer:
    """Split a complex prompt into specialist subtasks.

    The decomposer is stateless and deterministic — the same prompt always
    produces the same subtasks. This makes it testable and predictable.
    """

    def __init__(self, role_keywords: Optional[Dict[str, List[str]]] = None) -> None:
        """Build a decomposer.

        Args:
            role_keywords: Custom keyword map. Defaults to :data:`ROLE_KEYWORDS`.
        """
        self.role_keywords = role_keywords or ROLE_KEYWORDS

    def decompose(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> List[SubTask]:
        """Break ``prompt`` into subtasks.

        Args:
            prompt: The user's request.
            context: Optional extra data to attach to every subtask.

        Returns:
            Subtasks, in dependency order (independent tasks first).
        """
        prompt_lower = prompt.lower()
        matched_roles = [
            role for role, keywords in self.role_keywords.items()
            if any(kw in prompt_lower for kw in keywords)
        ]

        # If no roles matched, default to a general writer task
        if not matched_roles:
            matched_roles = ["writer"]

        subtasks: List[SubTask] = []
        for i, role in enumerate(matched_roles):
            subtasks.append(
                SubTask(
                    role=role,
                    instruction=self._instruction_for_role(role, prompt),
                    context=dict(context) if context else {},
                    depends_on=[],
                    priority=i,
                )
            )

        # Add implicit dependencies: research before write, write before edit/translate/summarize
        self._add_implicit_dependencies(subtasks)
        return subtasks

    def _instruction_for_role(self, role: str, prompt: str) -> str:
        """Return a role-specific instruction derived from the prompt."""
        instructions = {
            "researcher": f"Research and gather information for: {prompt}",
            "writer": f"Write content based on: {prompt}",
            "analyst": f"Analyze and evaluate: {prompt}",
            "planner": f"Create a plan or roadmap for: {prompt}",
            "editor": f"Edit and refine content for: {prompt}",
            "translator": f"Translate content for: {prompt}",
            "summarizer": f"Summarize the key points of: {prompt}",
            "fact_checker": f"Fact-check claims in: {prompt}",
            "brainstormer": f"Generate ideas for: {prompt}",
            "scheduler": f"Create a schedule or timeline for: {prompt}",
        }
        return instructions.get(role, f"Handle {role} task for: {prompt}")

    def _add_implicit_dependencies(self, subtasks: List[SubTask]) -> None:
        """Add ordering constraints based on role semantics."""
        role_to_index = {st.role: i for i, st in enumerate(subtasks)}

        # Research should come before write/analyze
        if "researcher" in role_to_index:
            for role in ("writer", "analyst", "summarizer", "fact_checker"):
                if role in role_to_index and role_to_index[role] > role_to_index["researcher"]:
                    subtasks[role_to_index[role]].depends_on.append("researcher")

        # Write should come before edit/translate/summarize
        if "writer" in role_to_index:
            for role in ("editor", "translator", "summarizer"):
                if role in role_to_index and role_to_index[role] > role_to_index["writer"]:
                    subtasks[role_to_index[role]].depends_on.append("writer")

        # Plan should come before schedule
        if "planner" in role_to_index and "scheduler" in role_to_index:
            if role_to_index["scheduler"] > role_to_index["planner"]:
                subtasks[role_to_index["scheduler"]].depends_on.append("planner")