"""Body/Mouth/Kilo — agent definitions.

Five agents, each a frozen record of what it is for, which model answers it, how
hard it may try, and whether it may produce file contents.

The shape follows Kilo Code's own agent format (markdown with YAML frontmatter, tool
permissions as glob rules, last match wins) because that format is MIT and because it
is a good one. It is adopted as *design*, not as a dependency: nothing here loads a Bun
binary, and nothing here can write to disk whatever the frontmatter says.

**No agent writes.** Two of the five — Architect and Reviewer — cannot even *propose*
file contents; the other three return text that :mod:`body.mouth.kilo.kilo_diff` turns
into a diff a human approves, and the only method in the package that writes is
:meth:`~body.mouth.kilo.kilo_diff.DiffStore.apply`, on a different object, under a
different permission row, needing a decision the model never sees.

That is the structural reason this design is safe rather than merely gated. A
permission row is something the user can tune; a write path that does not exist cannot
be tuned into existence by an edit to ``config/trust_levels.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from body.mouth.kilo.exceptions import AgentRefused

__all__ = ["AGENTS", "AGENT_NAMES", "Agent", "describe", "get", "writers"]

#: Cloud model for every agent, and the local model each falls back to. Groq is the
#: default for all five rather than Qwen for two of them: ~2 GB of VRAM competes with
#: Moondream under the 4.5 GB ceiling, and a rate-limit fallback that is always paid
#: for is not a fallback.
_MODEL = "groq/openai/gpt-oss-120b"
_FALLBACK = "qwen2.5-coder:3b"


@dataclass(frozen=True)
class Agent:
    """One coding agent.

    Attributes:
        name: The identifier the API and the tools use.
        role: One line, for the UI and the activity log.
        model: Provider-qualified model that answers this agent.
        fallback: Local model used when the cloud provider rate-limits.
        temperature: Sampling temperature. Low for the agents that read code.
        steps: How many tool passes this agent gets in one turn.
        can_write: True when this agent may produce **file contents as a proposal**.
            It does not mean the agent can write: no agent here has a method that
            writes to disk, whatever this flag says.
        tools: The glob permission rules, Kilo's format. Last match wins.
        system: The system prompt body.
    """

    name: str
    role: str
    model: str
    fallback: str
    temperature: float
    steps: int
    can_write: bool
    tools: Tuple[Tuple[str, str], ...]
    system: str

    def allows(self, tool: str) -> bool:
        """Return True when this agent may call a tool, last match winning.

        Only a trailing ``*`` is honoured, which is all Kilo's own rules need and all
        these agents use. A fuller glob would be a larger surface for no rule that
        exists.

        Args:
            tool: A tool name, or a glob such as ``bash:pytest *``.
        """
        verdict = "deny"
        for pattern, decision in self.tools:
            if pattern == "*" or pattern == tool or (
                pattern.endswith("*") and tool.startswith(pattern[:-1])
            ):
                verdict = decision
        return verdict == "allow"

    def describe(self) -> Dict[str, object]:
        """Return the agent as plain data, without the system prompt.

        The prompt is long and nobody reads it in a status panel.
        """
        return {
            "name": self.name, "role": self.role, "model": self.model,
            "fallback": self.fallback, "temperature": self.temperature,
            "steps": self.steps, "can_write": self.can_write,
            "permissions": dict(self.tools),
        }


def _agent(name: str, role: str, temperature: float, steps: int, can_write: bool,
           tools: Tuple[Tuple[str, str], ...], system: str) -> Agent:
    """Build an agent with the shared model pair.

    Args:
        name: The agent name.
        role: One line for the UI.
        temperature: Sampling temperature.
        steps: Tool-pass budget.
        can_write: Whether the agent may produce file contents.
        tools: The glob permission rules.
        system: The system prompt.

    Returns:
        The frozen definition.
    """
    return Agent(name=name, role=role, model=_MODEL, fallback=_FALLBACK,
                 temperature=temperature, steps=steps, can_write=can_write,
                 tools=tools, system=system)


AGENTS: Dict[str, Agent] = {
    agent.name: agent
    for agent in (
        _agent(
            "architect",
            "Decides the layout, the stack and the module interfaces. Writes nothing.",
            0.2, 1, False,
            (("*", "deny"), ("think", "allow"), ("read", "allow"), ("glob", "allow")),
            "You are a software architect. Produce a design, not code. For the "
            "request you are given return:\n1. FOLDER LAYOUT - every file to "
            "create, with its path.\n2. TECH STACK - language, framework, "
            "dependencies with versions.\n3. INTERFACES - the public function or "
            "class each module exposes.\n4. TESTS - what a test looks like.\nYou "
            "cannot write files. Describe; do not attempt to write.",
        ),
        _agent(
            "coder",
            "Implements the design as file contents. Proposes a diff; never writes.",
            0.15, 3, True,
            (("*", "deny"), ("think", "allow"), ("read", "allow"), ("glob", "allow"),
             ("write:propose", "allow")),
            "You are a coder. Implement the design you are given. Return ONLY file "
            "blocks, in this exact format and nothing else:\n"
            "```path:relative/path.py\n<the complete file contents>\n```\n"
            "Rules: one block per file; complete files, not patches; no prose "
            "before or after; no fence inside a fence. A file you do not name is a "
            "file that will not be created.",
        ),
        _agent(
            "reviewer",
            "Finds bugs, security problems and dead code. Reports, never fixes.",
            0.1, 2, False,
            (("*", "deny"), ("think", "allow"), ("read", "allow"), ("grep", "allow")),
            "You are a code reviewer. Report findings, never rewrite the code. "
            "Return a list, most severe first, each line exactly:\n"
            "SEVERITY|file:line|what is wrong|how to fix\nSEVERITY is one of: bug, "
            "security, quality, unused. Report only what you can point at. If there "
            "is nothing, say NONE.",
        ),
        _agent(
            "debugger",
            "Finds the root cause of a failure and proposes the fix. Never applies it.",
            0.1, 2, True,
            (("*", "deny"), ("think", "allow"), ("read", "allow"), ("grep", "allow"),
             ("write:propose", "allow")),
            "You are a debugger. Find the cause, not a nearby symptom. Work "
            "through: what the error says, what the code does, what differs between "
            "the two. State the cause in one sentence, then return the corrected "
            "file as a ```path:...``` block. Do not propose a change you cannot "
            "justify from the error you were given.",
        ),
        _agent(
            "tester",
            "Writes pytest tests for the code. Proposes them; the caller runs them.",
            0.2, 2, True,
            (("*", "deny"), ("think", "allow"), ("read", "allow"), ("glob", "allow"),
             ("write:propose", "allow")),
            "You are a tester. Write pytest tests for the code you are shown. Use "
            "```path:tests/test_<module>.py``` blocks. Cover the happy path, one "
            "boundary case and one failure case per function. Use plain asserts and "
            "the tmp_path fixture. You do not run anything; the caller runs it.",
        ),
    )
}

#: The agent names, in pipeline order: Architect decides, Coder builds, Reviewer
#: challenges, Tester verifies, and Debugger is called on failure.
AGENT_NAMES: Tuple[str, ...] = tuple(AGENTS)


def get(name: str) -> Agent:
    """Return one agent's definition.

    Args:
        name: The agent name.

    Returns:
        The definition.

    Raises:
        AgentRefused: There is no such agent. An unknown name is refused rather than
            defaulted, because defaulting would silently run the Architect's
            permissions against a request that expected the Coder's.
    """
    agent = AGENTS.get(str(name).strip().lower())
    if agent is None:
        raise AgentRefused(
            f"{name!r} is not a Kilo agent; expected one of " + ", ".join(AGENT_NAMES)
        )
    return agent


def describe() -> Dict[str, object]:
    """Return every agent definition. Not gated; a gauge.

    This is the panel a user reads to decide whether to trust the coding agents, so
    it is deliberately ungated — a policy nobody can see is a policy nobody can
    argue with.
    """
    return {
        "count": len(AGENTS),
        "order": list(AGENT_NAMES),
        "writers": sorted(name for name, agent in AGENTS.items() if agent.can_write),
        "agents": [AGENTS[name].describe() for name in AGENT_NAMES],
    }


def writers() -> Tuple[str, ...]:
    """Return the agents permitted to produce file contents.

    They *propose*: every one returns text that becomes a diff, and none has a method
    that writes.
    """
    return tuple(name for name in AGENT_NAMES if AGENTS[name].can_write)
