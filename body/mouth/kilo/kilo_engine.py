"""Body/Mouth/Kilo — the orchestrator.

:class:`KiloEngine` owns the collaborators, holds the order the checks run in, and
returns records rather than model objects. Nothing above this layer knows Groq exists.

**Propose, then decide, then call.** :meth:`KiloEngine.propose` builds the
:class:`~brain.prefrontal_cortex.action.Action` so the target string the gate decides
about is byte-identical to the one the workspace guard will insist on — the pattern
:class:`body.legs.BrowserActions` uses, for the same reason: a decision built from a
hand-written target is refused by the Body's own replay check, which is correct
behaviour and a miserable thing to debug.

**The pipeline is sequential.** Architect decides, Coder builds, Reviewer challenges,
Tester verifies, Debugger is called on failure. Not parallel, for two reasons that
happen to agree: the agents consume each other's output, and ZUZZ's 3 GB budget does
not allow two local models resident at once.

**Nothing here writes except :meth:`apply` and :meth:`rollback`,** which delegate to
:class:`~body.mouth.kilo.kilo_diff.DiffStore` and need both a ``modify_project`` and a
``write_files`` decision. Every other operation returns text, findings or a command
result.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import Action, ActionCategory

from body.mouth.kilo import kilo_agents as agents
from body.mouth.kilo.exceptions import (
    AgentRefused,
    DecisionRefused,
    PackageNameInvalid,
)
from body.mouth.kilo.kilo_config import KiloConfig, get_config
from body.mouth.kilo.kilo_diff import DiffStore, get_diff_store, parse_file_blocks
from body.mouth.kilo.kilo_git import KiloGit, get_git
from body.mouth.kilo.kilo_review import Reviewer, get_reviewer
from body.mouth.kilo.kilo_terminal import Terminal, get_terminal
from body.mouth.kilo.kilo_workspace import KiloWorkspace, get_workspace_guard

__all__ = ["CATEGORIES", "KiloEngine", "get_engine"]

#: The operations this engine dispatches, and the row each is judged under.
#:
#: ``preview_diff``, ``git_status``, ``workspace_tree`` and ``status`` are absent on
#: purpose. All four report ZUZZ's own state rather than acting on anything, and all
#: four are what a user reads *in order to decide* whether to approve — a gauge behind
#: a confirmation is a gauge nobody looked at. ``apply`` is the one that writes.
CATEGORIES = {
    "plan": "generate_code",
    "generate": "generate_code",
    "review": "read_files",
    "debug": "generate_code",
    "refactor": "generate_code",
    "tests": "execute_code",
    "apply": "modify_project",
    "rollback": "modify_project",
    "install": "install_packages",
}

#: A package name: a plain identifier, an optional extras marker, an optional version
#: specifier. Validated rather than escaped, because the command is an argument vector
#: with nothing to escape into.
_PACKAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}(?:\[[^\]]+\])?(?:[<>=!~][\w.\-+]+)?$"
)


class KiloEngine:
    """The coding agents, behind the gate.

    Attributes:
        workspace: The workspace guard.
        config: Model routing.
        diffs: The proposal store.
        git: Read-only git plus the approved mutating subset.
        terminal: The allowlisted command executor.
        reviewer: Static checks and the model review pass.
        files: The Body's FileManager, used for every read and write.
        logger: Activity logger.
    """

    def __init__(
        self,
        workspace: Optional[KiloWorkspace] = None,
        config: Optional[KiloConfig] = None,
        diffs: Optional[DiffStore] = None,
        git: Optional[KiloGit] = None,
        terminal: Optional[Terminal] = None,
        reviewer: Optional[Reviewer] = None,
        files: Optional[Any] = None,
        directories: Optional[Any] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        """Wire the engine, building any collaborator that was not handed in.

        Args:
            workspace: The workspace guard.
            config: Model routing.
            diffs: The proposal store.
            git: The git layer.
            terminal: The command executor.
            reviewer: The reviewer.
            files: The Body's FileManager, used by the proposal store and by every
                read this engine performs.
            directories: The Body's DirectoryManager, used to create parents.
            logger: Activity logger.
            kill_switch: The emergency stop.
        """
        self.logger = logger
        self.kill_switch = kill_switch
        self.files = files
        self.workspace = workspace or get_workspace_guard()
        self.config = config or get_config(logger=logger)
        self.diffs = diffs or get_diff_store(
            files=files, directories=directories, logger=logger, kill_switch=kill_switch
        )
        self.git = git or get_git(logger=logger, kill_switch=kill_switch)
        self.terminal = terminal or get_terminal(logger=logger, kill_switch=kill_switch)
        self.reviewer = reviewer or get_reviewer(config=self.config, logger=logger)

    # -- proposing -------------------------------------------------------

    def propose(
        self, operation: str, target: str = "", detail: Optional[Dict[str, Any]] = None
    ) -> Action:
        """Build the action a caller must get decided before calling this engine.

        Args:
            operation: One of :data:`CATEGORIES`.
            target: The concrete path, workspace, package or diff id.
            detail: Extra context for the Amygdala's scoring.

        Returns:
            The action, with a target the gate decides about identically to the one
            the guard will check.

        Raises:
            ValueError: For an operation this engine does not have.
        """
        if operation not in CATEGORIES:
            raise ValueError(
                f"unknown code operation {operation!r}; expected one of "
                + ", ".join(sorted(CATEGORIES))
            )
        return Action(
            category=ActionCategory(CATEGORIES[operation]),
            target=str(target or f"kilo:{operation}"),
            detail={"operation": operation, **(detail or {})},
        )

    def category_for(self, operation: str) -> str:
        """Return the row an operation maps onto.

        Args:
            operation: One of :data:`CATEGORIES`.

        Returns:
            The config key.

        Raises:
            ValueError: For an operation this engine does not have.
        """
        if operation not in CATEGORIES:
            raise ValueError(f"unknown code operation {operation!r}")
        return CATEGORIES[operation]

    # -- pipeline --------------------------------------------------------

    async def generate(
        self, decision: Any, description: str, workspace: str = "", agent: str = "coder"
    ) -> Dict[str, Any]:
        """Run the Architect then the Coder, and park the result as a proposal.

        The Architect runs first because a Coder given only "build me a Flask API"
        invents a file layout; given a layout, it fills one in. The design is returned
        alongside the diff rather than discarded, so a user can see *why* the files are
        shaped as they are.

        Nothing is written. The return value carries a ``diff_id``, and :meth:`apply`
        is a separate, separately-gated call.

        Args:
            decision: An allowed ``generate_code`` decision.
            description: What to build, in the user's words.
            workspace: Workspace name or path; ``""`` means the scratch root.
            agent: The implementing agent. Only a ``can_write`` agent is accepted.

        Returns:
            The proposal, the design, and which models answered.

        Raises:
            AgentRefused: The agent cannot produce files.
            WorkspaceRefused: The target is ZUZZ's own tree.
            DiffRefused: The model described the change rather than producing files.
        """
        target = self.workspace.resolve(workspace)
        implementation = agents.get(agent)
        if not implementation.can_write:
            raise AgentRefused(
                f"{implementation.name} does not produce files; use the coder, the "
                f"debugger or the tester"
            )
        design = await self.config.infer(decision, "architect", f"Design this:\n\n{description}")
        code = await self.config.infer(
            decision, agent,
            f"Design:\n{design.text[:8000]}\n\nImplement it exactly.\n\n{description}",
        )
        proposal = self.diffs.propose(
            target, parse_file_blocks(code.text), agent=agent, reader=self._reader(decision)
        )
        return {
            **proposal.to_dict(include_text=True),
            "design": design.text, "design_model": design.model, "code_model": code.model,
            "fell_back": code.fell_back or design.fell_back,
        }

    async def plan(self, decision: Any, description: str, workspace: str = "") -> Dict[str, Any]:
        """Ask the Architect for a design. Writes nothing.

        Args:
            decision: An allowed ``generate_code`` decision.
            description: What to build.
            workspace: Workspace name or path, resolved only to confirm it is legal.

        Returns:
            The design and which model wrote it.
        """
        target = self.workspace.resolve(workspace)
        result = await self.config.infer(decision, "architect", f"Design this:\n\n{description}")
        return {"workspace": target.to_dict(), "design": result.text, "model": result.model,
                "provider": result.provider, "fell_back": result.fell_back}

    async def review(self, decision: Any, path: str, workspace: str = "") -> Dict[str, Any]:
        """Review a file in a workspace. Writes nothing.

        Args:
            decision: An allowed ``read_files`` decision for the path.
            path: Workspace-relative path.
            workspace: Workspace name or path.

        Returns:
            The reviewer's record and the workspace it read from.

        Raises:
            DecisionRefused: No decision, so the read cannot be justified.
        """
        target = self.workspace.resolve(workspace)
        if decision is None:
            raise DecisionRefused("reviewing a file needs a read_files decision")
        source = str(self.files.read(decision, target.path(path))) if self.files else ""
        return {**await self.reviewer.review(decision, str(path), source),
                "workspace": target.to_dict()}

    async def debug(
        self, decision: Any, error: str, code: str = "", workspace: str = ""
    ) -> Dict[str, Any]:
        """Find the root cause of an error and propose the fix. Writes nothing.

        Args:
            decision: An allowed ``generate_code`` decision.
            error: The traceback or message.
            code: The offending source, when the caller has it.
            workspace: Workspace name or path.

        Returns:
            The explanation, plus the proposal when the Debugger returned whole files.
            A debugger that explains without producing a file is a normal outcome.
        """
        target = self.workspace.resolve(workspace)
        result = await self.config.infer(
            decision, "debugger",
            f"This failed:\n\n{error}\n\nThe code:\n\n{str(code)[:20_000]}",
        )
        blocks = parse_file_blocks(result.text)
        record: Dict[str, Any] = {
            "explanation": result.text[:2000] if blocks else result.text,
            "model": result.model, "provider": result.provider,
            "fell_back": result.fell_back, "workspace": target.to_dict(), "proposed": 0,
        }
        if blocks:
            proposal = self.diffs.propose(target, blocks, agent="debugger",
                                          reader=self._reader(decision))
            record.update(proposal.to_dict(include_text=True))
        return record

    async def refactor(
        self, decision: Any, path: str, goal: str, workspace: str = ""
    ) -> Dict[str, Any]:
        """Refactor a file towards a stated goal. Writes nothing.

        Args:
            decision: An allowed ``generate_code`` decision.
            path: Workspace-relative path to refactor.
            goal: What "better" means here, in the user's words.
            workspace: Workspace name or path.

        Returns:
            The proposal and the file's current contents, so the diff has a readable
            "before" beside it on the Code page.
        """
        target = self.workspace.resolve(workspace)
        return await self._rewrite(
            decision, target, path, "coder", goal,
            f"Refactor this file towards: {goal}",
            f"--- {path} ---\n", "Return the complete refactored file.", path,
        )

    async def write_tests(
        self, decision: Any, path: str, goal: str = "", workspace: str = ""
    ) -> Dict[str, Any]:
        """Have the Tester write tests for a file. Writes nothing.

        Separate from :meth:`run_tests` on purpose, and the two are not neighbours:
        this one proposes tests and is judged under ``generate_code``, while running
        them is ``execute_code``. Folding them together would put the running of code
        an agent wrote a moment earlier behind the same row as a test suite the user
        has been running all week, which is exactly the conflation ``execute_code``
        was created to refuse.

        Args:
            decision: An allowed ``generate_code`` decision.
            path: Workspace-relative path the tests are for. May be a package, a
                module or a single file.
            goal: What to cover — "the error paths" is more useful than blank.
            workspace: Workspace name or path.

        Returns:
            The proposal, the file's current contents, and where the tests were
            proposed to land.
        """
        target = self.workspace.resolve(workspace)
        where = self._test_path(target, path)
        return await self._rewrite(
            decision, target, path, "tester", goal or "cover the behaviour and the edges",
            f"Write pytest tests for: {path}\nGoal: {goal or 'cover behaviour and edges'}",
            f"--- {path} ---\n",
            f"Return the complete test file. It belongs at {where}.",
            where,
        )

    # -- running ---------------------------------------------------------

    def run_tests(self, decision: Any, workspace: str = "", target: str = "tests") -> Dict[str, Any]:
        """Run pytest in a workspace.

        Args:
            decision: An allowed ``execute_code`` decision.
            workspace: Workspace name or path.
            target: The test path or pattern handed to pytest.

        Returns:
            The command result, with the parsed per-test outcomes.

        Raises:
            DecisionRefused: No decision.
            CommandRefused: The vector is not runnable, or the path climbs out.
        """
        space = self.workspace.resolve(workspace)
        result = self.terminal.run(
            decision, ["pytest", str(target or "tests"), "-q"], space.root
        )
        return {"workspace": space.to_dict(), **result.to_dict()}

    def install_package(self, decision: Any, name: str, workspace: str = "") -> Dict[str, Any]:
        """Install a package into the workspace's environment.

        Args:
            decision: An allowed ``install_packages`` decision. That row asks at every
                trust level, so arriving without one is the normal case rather than
                the exceptional one.
            name: The package, optionally with a version specifier.
            workspace: Workspace name or path.

        Returns:
            The command result.

        Raises:
            DecisionRefused: No decision — that is, the user said no.
            PackageNameInvalid: The name is not a plain package identifier.
        """
        if decision is None:
            raise DecisionRefused(
                "installing a package needs an install_packages decision, and that "
                "row asks at every trust level (immutable rule R7)"
            )
        wanted = str(name or "").strip()
        if not wanted or not _PACKAGE.match(wanted):
            raise PackageNameInvalid(
                f"{name!r} is not a package name. Use a plain identifier, optionally "
                f"with a version specifier."
            )
        space = self.workspace.resolve(workspace)
        result = self.terminal.run(
            decision, ["pip", "install", wanted], space.root, timeout=600
        )
        return {"workspace": space.to_dict(), "package": wanted, **result.to_dict()}

    # -- writing ---------------------------------------------------------

    def apply(
        self, decision: Any, write_decision: Any, diff_id: str, force: bool = False
    ) -> Dict[str, Any]:
        """Write an approved proposal through Body/Fingers.

        Args:
            decision: An allowed ``modify_project`` decision.
            write_decision: An allowed ``write_files`` decision covering the workspace
                root.
            diff_id: The proposal to apply.
            force: Overwrite a file that changed since the preview.

        Returns:
            What was written.

        Raises:
            DecisionRefused: Either decision is missing or does not allow this.
            DiffNotFound: No such proposal.
            DiffRefused: A file changed since the preview and ``force`` is False.
        """
        return self.diffs.apply(decision, write_decision, diff_id, force=force)

    def rollback(self, decision: Any, write_decision: Any, diff_id: str) -> Dict[str, Any]:
        """Undo an applied proposal through Body/Fingers.

        Args:
            decision: An allowed ``modify_project`` decision.
            write_decision: An allowed ``write_files`` decision covering the root.
            diff_id: The proposal to undo.

        Returns:
            What was restored and what was trashed.
        """
        return self.diffs.rollback(decision, write_decision, diff_id)

    def reject(self, diff_id: str) -> Dict[str, Any]:
        """Discard a proposal without applying it. Not gated; nothing happened.

        Args:
            diff_id: The proposal to discard.

        Returns:
            Whether there was one to discard. Rejecting needs no permission: a user
            who says no has already exercised the authority that matters.
        """
        return {"diff_id": diff_id, "rejected": self.diffs.drop(diff_id)}

    # -- gauges ----------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return everything the Code page needs. Not gated; a gauge.

        Returns:
            The agents and their routing, the workspace policy and its workspaces,
            the pending diff count, the terminal allowlist, the git policy and the
            reviewer's capabilities. Reads ZUZZ's own state and nothing from inside a
            workspace.
        """
        return {
            "agents": agents.describe(), "routing": self.config.routing(),
            "workspace": self.workspace.describe(),
            "workspaces": self.workspace.list_workspaces(),
            "pending_diffs": self.diffs.preview()["count"],
            "terminal": self.terminal.limits(), "git": self.git.describe(),
            "reviewer": self.reviewer.capabilities(),
            "stopped": bool(self.kill_switch and self.kill_switch.is_killed()),
        }

    def preview_diff(self, diff_id: str = "") -> Dict[str, Any]:
        """Return a proposal, or all of them. Not gated; a gauge.

        Args:
            diff_id: One proposal, or ``""`` for every pending one.
        """
        return self.diffs.preview(diff_id)

    def workspace_tree(self, workspace: str = "", limit: int = 200) -> Dict[str, Any]:
        """Return a workspace's file tree. Not gated; a gauge.

        Args:
            workspace: Workspace name or path.
            limit: How many entries.
        """
        return self.workspace.listing(self.workspace.resolve(workspace), limit=limit)

    def git_status(self, workspace: str = "") -> Dict[str, Any]:
        """Return a workspace's git status. Not gated; a gauge.

        Args:
            workspace: Workspace name or path.
        """
        return self.git.status(self.workspace.resolve(workspace).root)

    # -- internals -------------------------------------------------------

    async def _rewrite(
        self,
        decision: Any,
        workspace: Any,
        path: str,
        agent: str,
        goal: str,
        instruction: str,
        source_prefix: str,
        tail: str,
        fallback_path: str,
    ) -> Dict[str, Any]:
        """Read a file, ask a writer agent for its replacement, and propose it.

        :meth:`refactor` and :meth:`write_tests` are the same three steps with
        different words, and the steps have two properties worth keeping in one place:
        the "before" text comes from Finger rather than from :func:`open` — a diff
        whose before-text was guessed produces a rollback that restores a fiction —
        and a model that answered without emitting file blocks still yields a
        proposal, because a rewrite of a file it chose not to fence is the whole file.

        Args:
            decision: The caller's decision.
            workspace: The resolved workspace.
            path: The workspace-relative path being rewritten.
            agent: Which writer agent to ask.
            goal: What "better" means, for the record and the log.
            instruction: The leading instruction, agent-specific.
            source_prefix: What introduces the pasted source in the prompt.
            tail: The closing instruction, usually naming the format wanted.
            fallback_path: Where the result goes when the model returned no blocks.

        Returns:
            The proposal, the goal, and the file's current contents.
        """
        source = self._maybe_read(decision, workspace.path(path))
        result = await self.config.infer(
            decision, agent,
            f"{instruction}\n\n{source_prefix}{source[:40_000]}\n\n{tail}",
        )
        proposal = self.diffs.propose(
            workspace,
            parse_file_blocks(result.text) or [(fallback_path, result.text)],
            agent=agent, reader=self._reader(decision),
        )
        return {
            **proposal.to_dict(include_text=True),
            "goal": goal, "original": source,
            "model": result.model, "provider": result.provider,
            "fell_back": result.fell_back,
        }

    @staticmethod
    def _test_path(workspace: Any, path: str) -> str:
        """Return where the tests for a path should live.

        ``tests/`` beside the module's own root, which is where the collection looks.
        A test file is *meant* to live in the test directory, so a proposal that put
        ``test_thing.py`` next to ``thing.py`` would be a test that silently never
        runs — worse than no test, because the suite stays green.

        A path that is *already* in a test directory is returned unchanged. Prefixing
        it a second time would produce ``tests/tests/test_test_thing.py``, which
        collects under the same name as the file it was meant to accompany.

        Args:
            workspace: The resolved workspace.
            path: The path being tested.

        Returns:
            A workspace-relative path.
        """
        parts = [piece for piece in str(path).replace("\\", "/").split("/") if piece]
        if not parts:
            return "tests/test_module.py"
        stem = parts[-1].rsplit(".", 1)[0] or "module"
        root = parts[0] if len(parts) > 1 else ""
        if root in ("tests", "test"):
            return "/".join(parts)
        return f"{root}/tests/test_{stem}.py" if root else f"tests/test_{stem}.py"

    def _maybe_read(self, decision: Any, absolute: str) -> str:
        """Read a file, tolerating a file that is not there yet.

        Args:
            decision: The caller's decision.
            absolute: The path to read.

        Returns:
            The text, or an empty string when it could not be read. A refactor of a
            file that does not exist is a create, and treating that as an error would
            refuse the one case where there is nothing to preserve.
        """
        if self.files is None:
            return ""
        try:
            return str(self.files.read(decision, absolute))
        except Exception:
            return ""

    def _reader(self, decision: Any) -> Optional[Any]:
        """Return a ``(path) -> text`` callable for diffing.

        The proposal store needs each file's current text to build an honest diff and
        an honest rollback, and that read goes through Finger with the caller's
        decision rather than through :func:`open`. A package that reads around the
        Body would be a package whose reads nobody audited.

        Args:
            decision: The caller's decision.

        Returns:
            The callable, or None when the engine has no FileManager.
        """
        if self.files is None:
            return None

        def reader(absolute: str) -> str:
            return str(self.files.read(decision, absolute))

        return reader


_ENGINE: Optional[KiloEngine] = None


def get_engine(
    files: Optional[Any] = None,
    directories: Optional[Any] = None,
    logger: Optional[Any] = None,
    kill_switch: Optional[Any] = None,
    workspace: Optional[KiloWorkspace] = None,
) -> KiloEngine:
    """Return the process-wide engine, building it on first use.

    Args:
        files: The Body's FileManager.
        directories: The Body's DirectoryManager.
        logger: Activity logger.
        kill_switch: The emergency stop.
        workspace: An existing workspace guard.
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = KiloEngine(workspace=workspace, files=files, directories=directories,
                             logger=logger, kill_switch=kill_switch)
    return _ENGINE
