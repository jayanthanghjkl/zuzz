"""Mouth — code generation and terminal command execution.

This package holds :mod:`body.mouth.kilo`, ZUZZ's coding engine: five specialist
agents, a workspace guard, proposal diffs, an allowlisted command executor, read-only
git and a static checker.

**It is ZUZZ-native, not a wrapper.** Kilo Code is a Bun-compiled TypeScript binary
with no Python SDK. Its ``edit`` and ``bash`` tools write to disk from inside its own
process, which would route around the PermissionGate, the Amygdala, Body/Fingers, the
TrashManager and the immutable activity log — and the only containment it offers is a
sandbox with no Windows backend, on a Windows machine. So this package adopts Kilo's
*design* (agents as markdown-plus-frontmatter, tool permissions as glob rules, a
plan → diff → approve → apply workflow) and implements it in Python, where ZUZZ owns
the write path. See ``docs/KILO_INTEGRATION_PLAN.md``.

**Every public method that can act takes a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` as its first
argument.** That list is closed, and ``tests/test_kilo_integration.py`` pins it with
``test_public_methods_that_act_all_require_a_decision``: a new decision-free method is
a test failure, not a quiet widening of the surface that skips the gate.

The exceptions are status, limits and policy — the panels a user reads *in order to
decide* whether to grant a permission. A gauge that needed a confirmation would be a
gauge nobody looked at, and the policy a coding agent refuses to show is a policy the
user cannot argue with:

* :func:`body.mouth.kilo.kilo_agents.describe`, :func:`body.mouth.kilo.kilo_agents.get`,
  :func:`body.mouth.kilo.kilo_agents.writers`, :meth:`Agent.allows`,
  :meth:`Agent.describe` — the agent catalogue.
* :meth:`KiloConfig.route`, :meth:`KiloConfig.routing` — model routing, and which
  cloud providers hold a credential right now.
* :meth:`KiloWorkspace.is_refused`, :meth:`KiloWorkspace.describe`,
  :meth:`KiloWorkspace.list_workspaces`, :meth:`KiloWorkspace.listing`,
  :meth:`Workspace.path`, :meth:`Workspace.to_dict`, :func:`is_inside`,
  :func:`get_workspace_guard` — the workspace policy.
* :func:`body.mouth.kilo.kilo_diff.parse_file_blocks`, :func:`build_change`,
  :func:`read_plain`, :meth:`DiffStore.propose`, :meth:`DiffStore.get`,
  :meth:`DiffStore.preview`, :meth:`DiffStore.drop`, :func:`get_diff_store` — proposals
  and previews. :meth:`DiffStore.drop` in particular: rejecting is the other half of
  approving, and a user who says no has already exercised the authority that matters.
* :meth:`KiloGit.category_for`, :meth:`KiloGit.describe`, :meth:`KiloGit.check`,
  :meth:`KiloGit.status`, :meth:`KiloGit.diff`, :meth:`KiloGit.log`,
  :func:`get_git` — git policy, and read-only git. ``git status`` is a read of the
  user's own history and maps to ``read_files``; filing it under ``git_operations``
  would mean asking about it.
* :meth:`Terminal.limits`, :meth:`Terminal.check`, :func:`get_terminal` — the command
  allowlist and the check a command passes before it runs.
* :func:`body.mouth.kilo.kilo_review.checks`, :func:`parse_model_findings`,
  :meth:`Reviewer.capabilities`, :func:`get_reviewer` — the deterministic checks are a
  pure function over a string, so there is nothing there to decide about.
* :meth:`KiloEngine.propose`, :meth:`KiloEngine.category_for`,
  :meth:`KiloEngine.reject`, :meth:`KiloEngine.status`, :meth:`KiloEngine.preview_diff`,
  :meth:`KiloEngine.workspace_tree`, :meth:`KiloEngine.git_status`, :func:`get_engine`
  — the assembler and its gauges. ``propose`` builds the decision rather than consuming
  one.

Five invariants run through every module here:

* **ZUZZ's own tree is read-only to the coding agents.** ``brain/``, ``body/``,
  ``face/``, ``core/``, ``config/``, ``tests/``, ``scripts/``, ``docs/`` and the three
  critical files are refused at every trust level — in
  :mod:`body.mouth.kilo.kilo_workspace`, as a refusal and not a clamp, because a
  silently relocated write is a write somewhere the user did not approve.
* **A proposal is text.** Only :meth:`DiffStore.apply` and :meth:`DiffStore.rollback`
  write, through Body/Fingers, under ``modify_project`` plus a ``write_files``
  decision for the workspace root.
* **A command is an argument vector**, drawn from a closed allowlist, with a timeout,
  and never ``shell=True``.
* **History rewriting is refused at every trust level**, because a trust row cannot
  say "never, because unrecoverable".
* **Installing a package asks at every level**, including 5 — immutable rule R7.
"""

from body.mouth.kilo.exceptions import (
    AgentRefused,
    CommandRefused,
    CommandTimedOut,
    DecisionRefused,
    DecisionReplayRefused,
DiffAlreadyApplied,
    DiffNotApplied,
    DiffNotFound,
    DiffRefused,
    DryRunRefused,
    GitRefused,
    InstallRefused,
    KillSwitchRefused,
    KiloRefused,
    ModelUnavailable,
    PackageNameInvalid,
    WorkspaceConfirmationRequired,
    WorkspaceNotFound,
    WorkspaceRefused,
)
from body.mouth.kilo.kilo_agents import AGENTS, AGENT_NAMES, Agent, describe, get, writers
from body.mouth.kilo.kilo_config import InferenceResult, KiloConfig, get_config
from body.mouth.kilo.kilo_diff import (
    FileChange,
    PendingDiff,
    DiffStore,
    build_change,
    get_diff_store,
    parse_file_blocks,
)
from body.mouth.kilo.kilo_engine import CATEGORIES, KiloEngine, get_engine
from body.mouth.kilo.kilo_git import READ_ONLY, REFUSED_SUBCOMMANDS, KiloGit, get_git
from body.mouth.kilo.kilo_review import Finding, Reviewer, checks, get_reviewer
from body.mouth.kilo.kilo_terminal import CommandResult, Terminal, get_terminal
from body.mouth.kilo.kilo_workspace import (
    CORE_DIRECTORIES,
    CRITICAL_FILES,
    KiloWorkspace,
    Workspace,
    get_workspace_guard,
)

__all__ = [
    "AGENTS",
    "AGENT_NAMES",
    "Agent",
    "AgentRefused",
    "CATEGORIES",
    "CORE_DIRECTORIES",
    "CRITICAL_FILES",
    "CommandRefused",
    "CommandResult",
    "CommandTimedOut",
    "DecisionRefused",
    "DecisionReplayRefused",
    "DiffAlreadyApplied",
    "DiffNotApplied",
    "DiffNotFound",
    "DiffRefused",
    "DiffStore",
    "DryRunRefused",
    "FileChange",
    "Finding",
    "GitRefused",
    "InferenceResult",
    "InstallRefused",
    "KillSwitchRefused",
    "KiloConfig",
    "KiloEngine",
    "KiloGit",
    "KiloRefused",
    "KiloWorkspace",
    "ModelUnavailable",
    "PackageNameInvalid",
    "PendingDiff",
    "READ_ONLY",
    "REFUSED_SUBCOMMANDS",
    "Reviewer",
    "Terminal",
    "Workspace",
    "WorkspaceConfirmationRequired",
    "WorkspaceNotFound",
    "WorkspaceRefused",
    "build_change",
    "checks",
    "describe",
    "get",
    "get_config",
    "get_diff_store",
    "get_engine",
    "get_git",
    "get_reviewer",
    "get_terminal",
    "get_workspace_guard",
    "parse_file_blocks",
    "writers",
]
