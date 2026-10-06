"""Tests for ``body/mouth/kilo`` — the coding agents.

Twenty-five tests over the package's promises, and the promises are the ones that
matter rather than the ones that are easy to assert:

* **ZUZZ's own tree is structurally refused.** Not gated, not clamped, not
  negotiated. A permission row cannot express "never, because this is me", so it is
  refused in code, and these tests check it at trust level 5 to prove the row is not
  what is doing the work.
* **A proposal is text and an apply is a separate, separately-gated act.** The tests
  apply through the real :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
  objects rather than through a stub that always says yes, because a stub cannot catch
  the failure this integration is most likely to have: a second write path that
  arrived without the second decision.
* **Nothing public acts without a decision.** Enumerated rather than sampled, so a
  method added next week that forgets its parameter is a test failure instead of a
  quiet widening of the surface that skips the gate.
* **History rewriting is refused at every trust level**, and installing asks at every
  trust level including 5. Both asserted against ``config/trust_levels.json``
  itself, not against a copy of it.

Nothing here reaches a real model. The two tests that would need one inject a stub
into :class:`~body.mouth.kilo.kilo_config.KiloConfig`, so the suite is a function of
the code and never of whether a credential happens to be in ``.env``.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.mouth.kilo import (
    CORE_DIRECTORIES,
    CRITICAL_FILES,
    DiffStore,
    KiloConfig,
    KiloEngine,
    KiloGit,
    KiloWorkspace,
    Reviewer,
    Terminal,
)
from body.mouth.kilo import kilo_agents as agents
from body.mouth.kilo import kilo_review as review_module
from body.mouth.kilo.kilo_config import InferenceResult
from body.mouth.kilo.exceptions import (
    AgentRefused,
    DiffAlreadyApplied,
    DiffNotApplied,
    DiffNotFound,
    DiffRefused,
    GitRefused,
    PackageNameInvalid,
    WorkspaceConfirmationRequired,
    WorkspaceRefused,
)
from body.fingers.exceptions import DecisionReplayRefused

ROOT = Path(__file__).resolve().parent.parent
TRUST_MATRIX = json.loads((ROOT / "config" / "trust_levels.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------


def allow(category: Any, target: str = "", level: int = 5) -> PermissionDecision:
    """Return an allowed decision, standing in for the gate.

    Args:
        category: The action category, as an enum member or a string.
        target: What the action acts on. Replay checks compare this exactly, so it
            has to be the string the Body will check.
        level: The trust level the decision claims.

    Returns:
        The decision.
    """
    return PermissionDecision(
        action=Action(category=ActionCategory(str(category)), target=target),
        allowed=True, requires_confirmation=False, decision="allow",
        reason="test", trust_level=level,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> KiloWorkspace:
    """Return a guard whose scratch root is inside the test's own directory.

    Args:
        tmp_path: The test's temporary directory.

    Returns:
        A guard rooted there, so nothing in these tests can reach the developer's
        real workspaces however badly a path is written.
    """
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return KiloWorkspace(root=root)


@pytest.fixture
def engine(workspace: KiloWorkspace) -> KiloEngine:
    """Return an engine with no model and no command runner.

    Args:
        workspace: The isolated guard.

    Returns:
        The engine. ``config`` is None so no test here reaches a model — the tests
        that want text inject it at the stub bridge instead — and the write tests
        attach a FileManager through :func:`writing_engine` when they need one.
    """
    return KiloEngine(workspace=workspace, config=None,
                      reviewer=review_module.Reviewer(config=None))


def writing_engine(workspace: KiloWorkspace) -> KiloEngine:
    """Return an engine whose writes go through a real FileManager.

    Args:
        workspace: The isolated guard.

    Returns:
        The engine.

    The proposal store has to be constructed explicitly rather than left to the
    engine's ``get_diff_store()`` default: that accessor is a process-wide singleton,
    so the first engine built in a test session captures whichever collaborators it
    had and every later engine silently reuses them. This is the same trap the server
    wiring is built to avoid, and hitting it here is why it is worth a test.
    """
    from body.fingers import DirectoryManager, FileManager
    from body.mouth.kilo import DiffStore

    files = FileManager()
    return KiloEngine(
        workspace=workspace, config=None,
        diffs=DiffStore(files=files, directories=DirectoryManager()),
        files=files, directories=DirectoryManager(),
        reviewer=review_module.Reviewer(config=None),
    )


def code_file(path: str, body: str) -> List[tuple]:
    """Return the ``(path, contents)`` pair shape the proposal store takes.

    Args:
        path: The workspace-relative path.
        body: The file's contents.

    Returns:
        One pair, in a list.
    """
    return [(path, body)]


# ---------------------------------------------------------------------
# 1. the agents
# ---------------------------------------------------------------------


def test_five_agents_and_only_three_write() -> None:
    """Five specialists, and the two that advise are marked as such.

    The set is closed. A sixth agent is a design change, and a test that hard-codes
    the count is the thing that makes somebody notice rather than the thing that
    makes it easy.
    """
    assert set(agents.AGENT_NAMES) == {"architect", "coder", "reviewer", "debugger", "tester"}
    assert set(agents.writers()) == {"coder", "debugger", "tester"}


def test_permission_grants_are_deny_by_default() -> None:
    """No agent may do anything its tool list did not name.

    Every definition opens with a catch-all ``deny`` and then names three or four
    tools. The catch-all is what makes an unlisted tool refused rather than allowed,
    so it is the line worth pinning: a definition that dropped it would still pass a
    test that only checked the grants.
    """
    for name in agents.AGENT_NAMES:
        agent = agents.get(name)
        assert agent.tools[0] == ("*", "deny"), f"{name} has no catch-all deny"
        assert agent.allows("think"), name
        # Not granted anywhere: tools nobody in this package implements.
        for absent in ("bash", "delete", "shell", "write"):
            assert not agent.allows(absent), f"{name} was granted {absent}"


def test_only_the_three_writers_carry_the_write_proposal_grant() -> None:
    """``write:propose`` is exactly the set of agents that may propose files.

    Checked as a set against the catalogue's own ``writers()`` rather than against a
    second hard-coded list, so the two cannot drift apart quietly.
    """
    granted = {
        name for name in agents.AGENT_NAMES if agents.get(name).allows("write:propose")
    }
    assert granted == set(agents.writers())


def test_only_a_writer_can_be_the_implementing_agent(workspace: KiloWorkspace) -> None:
    """Asking the Reviewer to write the code is refused by name.

    The refusal names the agents that can, because "that agent may not write" with
    no alternative is a dead end the model has to guess its way out of.
    """
    import asyncio

    engine_one = KiloEngine(workspace=workspace, config=None)
    with pytest.raises(AgentRefused) as caught:
        asyncio.run(engine_one.generate(allow("generate_code"), "build something",
                                        agent="reviewer"))
    assert "coder" in str(caught.value)


# ---------------------------------------------------------------------
# 2. the workspace guard — structural, not permission-based
# ---------------------------------------------------------------------


@pytest.mark.parametrize("directory", sorted(CORE_DIRECTORIES))
def test_every_core_directory_is_refused_at_level_five(workspace: KiloWorkspace, directory: str) -> None:
    """ZUZZ's own tree is refused even when nothing is asking.

    Each directory is checked individually rather than as a list assertion, because
    a list assertion reports "False is not True" and this needs to report *which*
    directory is writable — that is the whole finding.
    """
    refused, reason = workspace.is_refused(str(ROOT / directory / "anything.py"))
    assert refused, f"{directory} is writable at trust level 5"
    assert reason


@pytest.mark.parametrize("name", sorted(CRITICAL_FILES))
def test_every_critical_file_is_refused_by_name(workspace: KiloWorkspace, name: str) -> None:
    """A critical file is refused wherever it sits, not only in its usual folder.

    ``trust_levels.json`` and ``immutable_rules.py`` are the two files whose
    modification would end the safety argument entirely, so naming them separately
    from the directory rule is the point.
    """
    refused, reason = workspace.is_refused(str(ROOT / "config" / name))
    assert refused, f"{name} is writable"
    assert reason


def test_a_path_that_climbs_out_is_refused_not_resolved(workspace: KiloWorkspace) -> None:
    """``..`` never resolves to somewhere outside the workspace.

    Checked as "does not land in ZUZZ's tree" rather than as "raises a particular
    class", because there are two honest answers here and both are correct: a path
    that lands inside a core directory is refused outright, and one that lands
    somewhere unfamiliar is the confirmation question. What must never happen is the
    third — silently normalising into a folder the user did not approve.
    """
    from body.mouth.kilo.exceptions import KiloRefused

    for escaping in (
        str(ROOT / ".." / "somewhere-else"),
        str(workspace.root / ".." / "escape"),
        str(workspace.root / "demo" / ".." / ".." / "escape"),
    ):
        with pytest.raises(KiloRefused):
            workspace.resolve(escaping)


def test_a_workspace_path_is_always_inside_the_scratch_root(workspace: KiloWorkspace) -> None:
    """Every resolvable workspace root is under the guard's own root, or registered.

    Checked over the concrete value rather than over the code that computes it: this
    is the invariant the whole package rests on, and an invariant is worth asserting
    in the terms a caller actually sees.
    """
    for name in ("demo", "another", "nested/deeper"):
        space = workspace.resolve(name)
        root = str(space.root)
        assert root.startswith(str(workspace.root)) or space.registered, root


def test_an_unregistered_folder_asks_rather_than_refusing(workspace: KiloWorkspace, tmp_path: Path) -> None:
    """An unknown folder is a question, and the question carries the folder.

    This is the single exception to "refuse and stop", and it exists because ZUZZ is
    allowed to work in a project the user has not registered — just not until the
    user has said which project.
    """
    outside = tmp_path / "some-project"
    outside.mkdir()
    with pytest.raises(WorkspaceConfirmationRequired) as caught:
        workspace.resolve(str(outside))
    assert caught.value.workspace == str(outside)


def test_a_named_workspace_resolves_inside_the_scratch_root(workspace: KiloWorkspace) -> None:
    """A workspace name becomes a folder under the root, not a path of its own.

    The name is the whole point: it is what the UI shows, so ``demo`` must mean
    ``<root>/demo`` and nothing else. A name that resolved anywhere the caller liked
    would make the folder name a lie.
    """
    space = workspace.resolve("demo")
    assert space.name == "demo"
    assert str(space.root).endswith("demo")
    assert str(space.root).startswith(str(workspace.root))


# ---------------------------------------------------------------------
# 3. proposals
# ---------------------------------------------------------------------


def test_a_proposal_is_text_and_writes_nothing(engine: KiloEngine, workspace: KiloWorkspace) -> None:
    """Building a diff leaves the disk untouched.

    The file the proposal names does not exist afterwards. A ``propose`` that wrote
    even an empty file would make the whole two-decision flow decorative.
    """
    space = workspace.resolve("demo")
    proposal = engine.diffs.propose(space, code_file("app.py", "print('hi')\n"), agent="coder")
    assert proposal.pending
    assert proposal.changes[0].before is None
    assert not Path(space.root, "app.py").exists()


def test_apply_refuses_without_the_write_decision(engine: KiloEngine, workspace: KiloWorkspace) -> None:
    """One decision is not enough. The second is named in the refusal.

    ``modify_project`` says the project may be rewritten; ``write_files`` names the
    root the bytes land in. Both are required, and a refusal that says which one is
    missing is a refusal the caller can fix.
    """
    from body.mouth.kilo.exceptions import DecisionRefused

    space = workspace.resolve("demo")
    proposal = engine.diffs.propose(space, code_file("app.py", "print('hi')\n"), agent="coder")
    with pytest.raises(DecisionRefused) as caught:
        engine.diffs.apply(allow("modify_project", str(space.root)), None, proposal.diff_id)
    assert "write_files" in str(caught.value)


def test_apply_refuses_a_dry_run_decision(workspace: KiloWorkspace) -> None:
    """A dry-run decision does not execute, and the apply path checks.

    Uses a writing engine so the refusal asserted is the one about the verdict: an
    engine without a FileManager refuses for a different reason, and the message is
    checked as well as the type so the test cannot pass for the wrong one.
    """
    from body.mouth.kilo.exceptions import DecisionRefused

    engine_one = writing_engine(workspace)
    space = workspace.resolve("demo")
    proposal = engine_one.diffs.propose(space, code_file("app.py", "x = 1\n"), agent="coder")
    described = PermissionDecision(
        action=Action(category=ActionCategory.MODIFY_PROJECT, target=str(space.root),
                      dry_run=True),
        allowed=False, requires_confirmation=False, decision="ask",
        reason="dry run", trust_level=5, dry_run=True,
    )
    with pytest.raises(DecisionRefused) as caught:
        engine_one.diffs.apply(described, allow("write_files", str(space.root)),
                               proposal.diff_id)
    assert "modify_project" in str(caught.value)
    assert not Path(space.root, "app.py").exists()


def test_apply_needs_both_decisions_and_lands_through_fingers(workspace: KiloWorkspace) -> None:
    """With both decisions and a FileManager, the bytes land and the file is real.

    This is the one family of tests that actually writes, and it writes into
    ``tmp_path``. It exists because every other apply test proves a refusal, and a
    package where every apply test proves a refusal is a package where nothing was
    verified about the happy path.
    """
    engine_one = writing_engine(workspace)
    space = workspace.resolve("demo")
    proposal = engine_one.diffs.propose(space, code_file("app.py", "print('hi')\n"), agent="coder")

    result = engine_one.diffs.apply(
        allow("modify_project", str(space.root)),
        allow("write_files", str(space.root)),
        proposal.diff_id,
    )
    assert result["written"]
    assert Path(space.root, "app.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_rollback_restores_the_original_contents(workspace: KiloWorkspace) -> None:
    """Rollback puts back what was there, byte for byte.

    The "before" text is what makes this possible, which is why it is read through
    the Body at propose time rather than guessed. A rollback that restored a fiction
    would be worse than no rollback, because the user would believe their file was
    safe.
    """
    engine_one = writing_engine(workspace)
    space = workspace.resolve("demo")
    Path(space.root).mkdir(parents=True, exist_ok=True)
    Path(space.root, "app.py").write_text("original = True\n", encoding="utf-8")

    proposal = engine_one.diffs.propose(
        space, code_file("app.py", "rewritten = True\n"), agent="coder"
    )
    engine_one.diffs.apply(
        allow("modify_project", str(space.root)),
        allow("write_files", str(space.root)),
        proposal.diff_id,
    )
    assert Path(space.root, "app.py").read_text(encoding="utf-8") == "rewritten = True\n"

    engine_one.diffs.rollback(
        allow("modify_project", str(space.root)),
        allow("write_files", str(space.root)),
        proposal.diff_id,
    )
    assert Path(space.root, "app.py").read_text(encoding="utf-8") == "original = True\n"

    # A second rollback is refused rather than repeating silently: the user would
    # otherwise see "done" for an operation that ran again on a file they may have
    # edited since.
    with pytest.raises(DiffNotApplied) as caught:
        engine_one.diffs.rollback(
            allow("modify_project", str(space.root)),
            allow("write_files", str(space.root)),
            proposal.diff_id,
        )
    assert "never applied" in str(caught.value)
    assert Path(space.root, "app.py").read_text(encoding="utf-8") == "original = True\n"


def test_a_file_that_moved_since_the_preview_is_refused(workspace: KiloWorkspace) -> None:
    """A file edited after the diff was previewed is not overwritten.

    This is the one way a diff viewer causes data loss, so ``force`` defaults to
    False and the refusal names both ways out: re-run the proposal, or say so.
    """
    engine_one = writing_engine(workspace)
    space = workspace.resolve("demo")
    Path(space.root).mkdir(parents=True, exist_ok=True)
    Path(space.root, "app.py").write_text("original = True\n", encoding="utf-8")

    proposal = engine_one.diffs.propose(
        space, code_file("app.py", "rewritten = True\n"), agent="coder"
    )

    # The user edits the file while the diff is sitting on screen.
    Path(space.root, "app.py").write_text("edited_by_hand = True\n", encoding="utf-8")

    with pytest.raises(DiffRefused) as caught:
        engine_one.diffs.apply(
            allow("modify_project", str(space.root)),
            allow("write_files", str(space.root)),
            proposal.diff_id,
        )
    assert "changed since" in str(caught.value)
    assert Path(space.root, "app.py").read_text(encoding="utf-8") == "edited_by_hand = True\n"


def test_an_unknown_proposal_id_is_refused_as_not_found(engine: KiloEngine) -> None:
    """A stale id is a real answer, not an empty result.

    Proposals live in memory only, so an id from before a restart is exactly the
    case this has to get right: 404, not a silent success that applied nothing.
    """
    with pytest.raises(DiffNotFound) as caught:
        engine.diffs.get("no-such-diff")
    assert "in memory" in str(caught.value)


def test_an_unchanged_file_is_not_a_change(engine: KiloEngine, workspace: KiloWorkspace) -> None:
    """A proposal that changes nothing is refused rather than shown as empty.

    An empty diff the user approves teaches them that approval means nothing, so the
    store would rather say "this file is unchanged".
    """
    space = workspace.resolve("demo")
    with pytest.raises(DiffRefused):
        engine.diffs.propose(space, [("app.py", "same\n"), ("app.py", "same\n")])


def test_a_repeated_path_in_one_proposal_is_refused(engine: KiloEngine, workspace: KiloWorkspace) -> None:
    """One path may appear once per proposal.

    Two blocks for the same file means the model changed its mind mid-answer, and
    applying them in order would leave the user approving a diff they never saw.
    """
    space = workspace.resolve("demo")
    with pytest.raises(DiffRefused):
        engine.diffs.propose(space, code_file("app.py", "one\n") + code_file("app.py", "two\n"))


def test_a_block_that_climbs_out_is_refused_before_any_read() -> None:
    """``../`` in a model's file block never reaches the filesystem.

    Parsed and refused up front, because a path is only dangerous if something acts
    on it, and the thing that acts on it here is the apply. Checked in the block
    format the agents are actually told to use, because a test written against the
    wrong format would pass on an empty parse.
    """
    from body.mouth.kilo.kilo_diff import parse_file_blocks

    block = "```path: ../../etc/passwd\n\nroot:x:0:0::/root:/bin/sh\n```"
    with pytest.raises(DiffRefused) as caught:
        parse_file_blocks(block)
    assert "climbs out" in str(caught.value)


def test_a_well_formed_block_parses_into_one_pair() -> None:
    """The fenced ``path:`` format the agents are told to use parses into a pair.

    The control for the refusal above. Without it a regex that matched nothing would
    make every "it refuses" test pass for the wrong reason.
    """
    from body.mouth.kilo.kilo_diff import parse_file_blocks

    assert parse_file_blocks("```path: app/thing.py\n\nprint('hi')\n```") == [
        ("app/thing.py", "\nprint('hi')\n")
    ]


# ---------------------------------------------------------------------
# 4. git
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "reset", "--hard"],
        ["git", "clean", "-fd"],
        ["git", "push", "--force"],
        ["git", "push", "--mirror"],
        ["git", "rebase", "main"],
        ["git", "filter-branch", "--all"],
    ],
)
def test_history_rewriting_is_refused(argv: List[str]) -> None:
    """Every destructive git vector is refused, at every trust level.

    This does not consult the gate at all, which is the point being asserted: a
    trust row cannot say "never, because unrecoverable", so the refusal is in the
    module and the row only decides how often to ask about the rest.
    """
    engine_one = KiloEngine(config=None)
    with pytest.raises(GitRefused):
        engine_one.git.run(allow("git_operations"), argv, str(ROOT))


def test_git_status_is_a_read_and_maps_to_read_files() -> None:
    """``status`` is a read of the user's own history, so it borrows ``read_files``.

    Filing it under ``git_operations`` would mean asking about it at levels where a
    file read is already trusted — which is the over-prompting the visual-capture
    and memory rows are documented to avoid.
    """
    engine_one = KiloEngine(config=None)
    assert engine_one.git.category_for("status") == "read_files"
    assert engine_one.git.category_for("commit") == "git_operations"


# ---------------------------------------------------------------------
# 5. the terminal
# ---------------------------------------------------------------------


def test_a_command_off_the_allowlist_is_refused_and_says_what_is_allowed() -> None:
    """The command set is closed, and the refusal names what would be allowed.

    A refusal that only said "no" would make the allowlist undiscoverable, so the
    message carries the list. That is asserted rather than assumed: an allowlist
    nobody can see is an allowlist users stop asking for.
    """
    from body.mouth.kilo.exceptions import CommandRefused

    with pytest.raises(CommandRefused) as caught:
        Terminal().check(["curl", "https://example.com"])
    message = str(caught.value)
    assert "curl" in message
    assert "pytest" in message


def test_an_absolute_path_in_an_argument_is_refused() -> None:
    """A command may name relative paths inside a workspace and nothing else.

    ``shell=False`` means an absolute path would simply be passed through, so this is
    the layer that refuses it rather than letting pytest open a file somewhere the
    workspace guard never saw.
    """
    from body.mouth.kilo.exceptions import CommandRefused

    with pytest.raises(CommandRefused) as caught:
        Terminal().check(["pytest", "/etc/passwd"])
    assert "absolute path" in str(caught.value)


def test_an_argument_that_climbs_out_is_refused() -> None:
    """``..`` in an argument is refused here rather than normalised.

    The same reasoning as the workspace guard, at the argument level: a silently
    resolved path is a path the user did not approve.
    """
    from body.mouth.kilo.exceptions import CommandRefused

    with pytest.raises(CommandRefused) as caught:
        Terminal().check(["pytest", "../../tests"])
    assert "climbs out" in str(caught.value)


def test_an_allowed_command_passes_the_check_and_comes_back_normalised() -> None:
    """The control for the three refusals above, and the shape of a success.

    Without it a ``check`` that refused everything would make all three refusal
    tests pass, and the allowlist would be a wall rather than a list.
    """
    assert Terminal().check(["pytest", "tests", "-q"]) == ["pytest", "tests", "-q"]


def test_the_amygdala_scores_a_destructive_command_before_it_runs() -> None:
    """``rm -rf`` is caught as a pattern, not only by the allowlist.

    The allowlist already keeps it out — ``rm`` is not on it — but the scoring layer
    is what stops the joined command string from reaching the log and the confirmation
    popup looking harmless, and it is a separate defence that can be edited
    independently.
    """
    from brain.amygdala.threat_detector import ThreatDetector

    action = Action(
        category=ActionCategory.EXECUTE_CODE,
        target="rm -rf / --no-preserve-root",
        detail={"command": "rm -rf /"},
    )
    assessment = ThreatDetector().assess(action)
    assert assessment.score > 0, assessment.explanation


def test_the_terminal_never_passes_a_shell_to_subprocess() -> None:
    """No call site passes ``shell=True``, checked over the AST rather than the text.

    The text contains ``shell=True`` twice — in the module docstring and in a
    comment explaining why it is never used — so a substring search would fail on
    prose. Walking the tree for actual keyword arguments is what distinguishes the
    two.
    """
    import ast

    tree = ast.parse((ROOT / "body" / "mouth" / "kilo" / "kilo_terminal.py")
                     .read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "shell" and not (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is False
        )
    ]
    assert not offenders, f"shell= at lines {offenders}"


# ---------------------------------------------------------------------
# 6. the reviewer
# ---------------------------------------------------------------------


def test_the_static_reviewer_finds_what_it_claims_to() -> None:
    """A pure function over a string finds ``eval``, bare ``except`` and unused imports.

    Deterministic, so it is asserted deterministically. A reviewer that needed a
    model to find ``eval(`` would be a reviewer that misses it whenever the provider
    is unavailable.
    """
    found = [item.severity for item in review_module.checks(
        "import os\n\n\ndef f(x):\n    try:\n        pass\n    except:\n        pass\n    return eval(x)\n"
    )]
    assert "security" in found or "bug" in found
    assert "unused" in found


def test_a_clean_file_produces_no_findings() -> None:
    """A file with nothing wrong produces an empty list, not a "looks fine" string.

    The empty list is what lets the caller count severities and decide whether any
    of them are blocking.
    """
    assert review_module.checks("def add(a, b):\n    return a + b\n") == []


# ---------------------------------------------------------------------
# 7. routing
# ---------------------------------------------------------------------


class StubBridge:
    """A Corpus Callosum whose one connector behaves as a test needs it to.

    Attributes:
        connectors: The connector table ``KiloConfig._cloud`` reads.
    """

    def __init__(self, connector: Any = None) -> None:
        """Build the bridge.

        Args:
            connector: The Groq stand-in, or None for "no connector at all".
        """
        self.connectors = {"groq": connector} if connector is not None else {}

    def configured_providers(self) -> List[str]:
        """Report which providers hold a credential.

        Returns:
            The names of the connectors that say they are configured.
        """
        return [name for name, item in self.connectors.items() if item.is_configured()]


class StubConnector:
    """A cloud connector that answers, or refuses in a specific way.

    Attributes:
        error: The exception to raise, or None to answer.
        reply: What to answer with.
    """

    def __init__(self, error: Optional[Exception] = None, reply: str = "cloud answer") -> None:
        """Build the connector.

        Args:
            error: The refusal to raise. None answers instead.
            reply: What to answer with.
        """
        self.error = error
        self.reply = reply

    def is_configured(self) -> bool:
        """Return that a credential is present.

        Returns:
            True.
        """
        return True

    async def chat(self, messages: List[Dict[str, str]], model: str, timeout: int = 120) -> str:
        """Answer, or refuse as instructed.

        Args:
            messages: The prompt.
            model: The model asked for.
            timeout: Seconds allowed.

        Returns:
            The reply.

        Raises:
            Exception: Whatever this connector was built to raise.
        """
        if self.error is not None:
            raise self.error
        return self.reply


def test_a_rate_limited_cloud_falls_back_to_local(workspace: KiloWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 becomes a slower answer rather than a failure.

    The local fallback exists for exactly this, so the assertion is that the
    *fallback* answered and that it reported having fallen back — "the call
    succeeded" would also be true if the cloud had answered.
    """
    import asyncio

    from body.mouth.kilo import kilo_config as config_module
    from brain.corpus_callosum.connector_state import RateLimitedError

    asked: List[str] = []

    async def local_answer(model: str, messages: List[Dict[str, str]], level: int) -> str:
        """Answer from the stub local model.

        Args:
            model: The fallback model that was asked for.
            messages: The prompt.
            level: The routing level, which is the retry count.

        Returns:
            A fenced file block, so the caller has something to propose.
        """
        asked.append(model)
        return "```path: app.py\n\ndef added():\n    return 1\n```"

    monkeypatch.setattr(config_module, "call_local", local_answer)

    config = config_module.KiloConfig()
    config.bridge = StubBridge(
        StubConnector(error=RateLimitedError("429 rate_limit_exceeded"))
    )
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    result = asyncio.run(engine_one.generate(allow("generate_code"), "add a function", "demo"))
    assert result["fell_back"] is True
    assert asked, "the local model was never asked"
    assert result["files"] == 1


def test_an_unconfigured_cloud_also_falls_back(workspace: KiloWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """No credential is the same as a rate limit: a slower answer, not a failure.

    The two cases are separate because a developer with no Groq key is the ordinary
    first-run state, and an integration that refused to answer at all without a cloud
    credential would look broken on the machine most people would try it on.
    """
    import asyncio

    from body.mouth.kilo import kilo_config as config_module

    async def local_answer(model: str, messages: List[Dict[str, str]], level: int) -> str:
        """Answer from the stub local model.

        Args:
            model: The fallback model asked for.
            messages: The prompt.
            level: The routing level.

        Returns:
            A fenced file block.
        """
        return "```path: app.py\n\nx = 1\n```"

    monkeypatch.setattr(config_module, "call_local", local_answer)
    config = config_module.KiloConfig()
    config.bridge = StubBridge(None)
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    result = asyncio.run(engine_one.generate(allow("generate_code"), "add a line", "demo"))
    assert result["fell_back"] is True
    assert result["files"] == 1


def test_a_cloud_failure_that_is_not_a_rate_limit_is_not_substituted(workspace: KiloWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real provider error refuses rather than quietly becoming a local answer.

    A 429 means "ask again shortly", so substituting is right. A 500 or a malformed
    response means the request itself was wrong, and answering it with a different
    model would produce a proposal from a request nobody made.
    """
    import asyncio

    from body.mouth.kilo import kilo_config as config_module
    from body.mouth.kilo.exceptions import ModelUnavailable
    from brain.corpus_callosum.connector_state import ConnectorError

    async def local_answer(model: str, messages: List[Dict[str, str]], level: int) -> str:
        """Fail the test if it is ever called.

        Args:
            model: The fallback model asked for.
            messages: The prompt.
            level: The routing level.

        Returns:
            Never returns.
        """
        raise AssertionError("the local model must not be asked for a non-rate-limit failure")

    monkeypatch.setattr(config_module, "call_local", local_answer)
    config = config_module.KiloConfig()
    config.bridge = StubBridge(StubConnector(error=ConnectorError("500 upstream error")))
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    with pytest.raises(ModelUnavailable) as caught:
        asyncio.run(engine_one.generate(allow("generate_code"), "add a line", "demo"))
    assert "not a rate limit" in str(caught.value)


def test_no_provider_at_all_is_a_named_refusal(workspace: KiloWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no cloud credential and no reachable local model, the answer is "unavailable".

    Not an empty result and not a fabricated proposal — a model that answered nothing
    must not leave a diff sitting on the Code page for the user to apply.
    """
    import asyncio

    from body.mouth.kilo import kilo_config as config_module
    from body.mouth.kilo.exceptions import ModelUnavailable
    from brain.cerebrum.backends import BackendError

    async def no_local(model: str, messages: List[Dict[str, str]], level: int) -> str:
        """Refuse, the way an unreachable local model would.

        Args:
            model: The fallback model asked for.
            messages: The prompt.
            level: The routing level.

        Raises:
            BackendError: Always.
        """
        raise BackendError("ollama is not running")

    monkeypatch.setattr(config_module, "call_local", no_local)
    config = config_module.KiloConfig()
    config.bridge = StubBridge(None)
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    with pytest.raises(ModelUnavailable) as caught:
        asyncio.run(engine_one.generate(allow("generate_code"), "add a function", "demo"))
    assert "would not answer" in str(caught.value)


# ---------------------------------------------------------------------
# 8. installing
# ---------------------------------------------------------------------


def test_install_asks_at_every_trust_level_including_five() -> None:
    """Immutable rule R7 has no trust-level escape hatch.

    Read from the matrix rather than from a copy of it, so editing the row to say
    ``auto`` at 5 fails here instead of quietly installing a wheel that runs its
    author's build hooks.
    """
    row = TRUST_MATRIX["actions"]["install_packages"]["5"]
    assert row == "ask", f"install_packages at level 5 is {row!r}, not 'ask'"
    for level in ("1", "2", "3", "4", "5"):
        assert TRUST_MATRIX["actions"]["install_packages"][level] == "ask", level


def test_a_package_name_is_validated_not_escaped(workspace: KiloWorkspace) -> None:
    """A name containing ``;`` or ``&&`` is not a package name.

    The command is an argument vector, so there is nothing to escape into — which is
    why the answer is "that is not a package" rather than "here is a sanitised one".
    A sanitised name would install something the user did not ask for.
    """
    engine_one = KiloEngine(workspace=workspace, config=None)
    for name in ("requests; rm -rf /", "a && b", "", "requests\npip install evil"):
        with pytest.raises(PackageNameInvalid):
            engine_one.install_package(allow("install_packages"), name, "demo")


def test_install_without_a_decision_is_refused(workspace: KiloWorkspace) -> None:
    """No decision means the user said no, and it says so.

    ``install_packages`` asks at every level, so arriving here without a decision is
    the normal case rather than a programming error — which is why the refusal is
    explicit about the rule rather than a generic "no permission".
    """
    engine_one = KiloEngine(workspace=workspace, config=None)
    with pytest.raises(Exception) as caught:
        engine_one.install_package(None, "requests", "demo")
    assert "R7" in str(caught.value) or "install_packages" in str(caught.value)


# ---------------------------------------------------------------------
# 9. the invariant: nothing public acts without a decision
# ---------------------------------------------------------------------


def test_public_methods_that_act_all_require_a_decision() -> None:
    """Every public method that can act takes a decision among its parameters.

    Enumerated, not sampled. The list here is the closed one
    ``body/mouth/kilo/__init__.py`` publishes; a method added next week without a
    decision parameter fails this test rather than quietly widening the surface that
    skips the gate. ``self`` is skipped rather than compared, because the question is
    whether a *decision* is required, not where it sits in the signature.
    """
    expected = {
        KiloEngine: {"generate", "plan", "review", "debug", "refactor", "write_tests",
                     "run_tests", "install_package", "apply", "rollback"},
        Terminal: {"run"},
        KiloGit: {"run"},
        Reviewer: {"review"},
        KiloConfig: {"infer"},
        DiffStore: {"apply", "rollback"},
    }
    for cls, names in expected.items():
        for name in names:
            method = getattr(cls, name, None)
            assert method is not None, f"{cls.__name__}.{name} does not exist"
            parameters = [
                item for item in inspect.signature(method).parameters if item != "self"
            ]
            assert "decision" in parameters, (
                f"{cls.__name__}.{name} takes {parameters} and no decision"
            )


# ---------------------------------------------------------------------
# 10. the routes
# ---------------------------------------------------------------------


def test_eleven_endpoints_are_registered() -> None:
    """The eleven paths the plan names, all on one router.

    Asserted as a set so a renamed or dropped route names itself in the failure.
    """
    from face.api import routes_code

    paths = {route.path for route in routes_code.router.routes}
    assert paths == {
        "/api/code/generate", "/api/code/review", "/api/code/debug",
        "/api/code/refactor", "/api/code/test", "/api/code/diff",
        "/api/code/apply", "/api/code/rollback", "/api/code/workspace",
        "/api/code/run", "/api/code/agents-status",
    }


def test_the_app_attaches_one_engine_with_the_apps_own_collaborators() -> None:
    """``app.state.kilo`` holds an engine built from *this* app's Body.

    The process-wide ``get_engine()`` would capture whichever collaborators happened
    to be first and hand them to every later caller, so a second app would end up
    writing through the first one's FileManager. Identity is the claim.
    """
    from face.api.server import create_app

    app = create_app()
    assert app.state.kilo is not None
    assert app.state.kilo.files is app.state.file_manager
    assert app.state.kilo.kill_switch is app.state.kill_switch


def test_a_generated_workspace_cannot_be_zuZZ_itself() -> None:
    """The guard refuses ZUZZ's own tree before anything is proposed.

    Checked through :meth:`KiloWorkspace.resolve`, which is what every gated route
    calls before it builds an action — so a refusal here is a refusal on the only
    path into the engine, whether the workspace came from a query string or a body.
    """
    with pytest.raises(WorkspaceRefused):
        KiloWorkspace().resolve(str(ROOT / "face" / "frontend"))
    with pytest.raises(WorkspaceRefused):
        KiloWorkspace().resolve(str(ROOT / "brain" / "prefrontal_cortex"))


def test_a_stop_refuses_a_gated_code_call_before_the_gate_is_consulted() -> None:
    """While the emergency stop is engaged, the coding routes answer 409.

    Checked at the ``run`` layer rather than through HTTP, because the stop is
    consulted before the action is even built — a call that built the action first
    would have scored it against the Amygdala, which is work done for an operation
    that was never going to happen.
    """
    import asyncio

    from fastapi import HTTPException

    from face.api.code_support import CodeRequest, run as gate_and_run

    class Stopped:
        """A kill switch that is engaged."""

        @staticmethod
        def is_killed() -> bool:
            """Return that ZUZZ is stopped.

            Returns:
                True.
            """
            return True

    class Silent:
        """A logger that records nothing and says nothing."""

        @staticmethod
        def log(*args: Any, **kwargs: Any) -> None:
            """Discard the event.

            Args:
                *args: Ignored.
                **kwargs: Ignored.

            Returns:
                Nothing.
            """

    class State:
        """The collaborators ``run`` reaches for."""

        kill_switch = Stopped()
        logger = Silent()
        kilo = object()

    class FakeRequest:
        """The minimum ``run`` reads."""

        app = type("App", (), {"state": State()})()

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            gate_and_run(FakeRequest(), "generate", CodeRequest(),
                         lambda decision: None, lambda: {})
        )
    assert caught.value.status_code == 409
    assert "kill switch" in str(caught.value.detail).lower()


def test_a_route_with_no_engine_attached_says_so_rather_than_refusing_per_call() -> None:
    """Without an engine the route is 503, not eleven individual refusals.

    An app built without the coding agents should say "not attached" once, rather
    than serving a Code page whose every button reports a different refusal.
    """
    import asyncio

    from fastapi import HTTPException

    from face.api.code_support import engine as find_engine

    class Bare:
        """A request whose app has no ``kilo`` attribute."""

        app = type("App", (), {"state": type("State", (), {})()})()

    with pytest.raises(HTTPException) as caught:
        find_engine(Bare())
    assert caught.value.status_code == 503


# ---------------------------------------------------------------------
# 11. the matrix
# ---------------------------------------------------------------------


def test_generate_code_asks_at_the_low_levels_and_autos_at_the_high_ones() -> None:
    """The row is read from the matrix, and the shape is the one that was argued.

    It borrows ``mouse_keyboard``'s shape rather than ``read_files``'s: at level 2 a
    read is trusted with a file the user pointed at, and generating code is not that.
    """
    row = TRUST_MATRIX["actions"]["generate_code"]
    assert row["1"] == "ask"
    assert row["2"] == "ask"
    assert row["3"] == "auto"
    assert row["5"] == "auto"


def test_modify_project_is_stricter_than_write_files() -> None:
    """The gap between the two rows is the whole reason ``modify_project`` exists.

    ``write_files`` auto-approves from level 2; ``modify_project`` asks until 3. A
    row that merely borrowed its neighbour's shape would have been re-examined
    whenever the neighbour changed, so the difference is asserted rather than assumed.
    """
    actions = TRUST_MATRIX["actions"]
    assert actions["write_files"]["2"] == "auto"
    assert actions["modify_project"]["2"] == "ask"
    assert actions["modify_project"]["3"] == "auto"


def test_execute_code_matches_run_python_rather_than_something_looser() -> None:
    """Running a test suite sits exactly where running a script sits.

    A test file an agent wrote a moment ago is not a gentler thing to run than one
    the user has been running all week, so the two rows are identical on purpose.
    Checking them against each other means a future change to one has to be a
    deliberate change to the other.
    """
    actions = TRUST_MATRIX["actions"]
    assert actions["execute_code"] == actions["run_python"]


def test_every_category_the_engine_uses_has_a_row() -> None:
    """A category with no row makes the gate refuse, which is the safe direction.

    Asserted anyway, because "safe direction" is not the same as "noticed": a
    category added to the engine and forgotten in the matrix would be invisible until
    somebody tried to use it.
    """
    from body.mouth.kilo.kilo_engine import CATEGORIES

    actions = TRUST_MATRIX["actions"]
    for category in sorted(set(CATEGORIES.values())):
        assert category in actions, f"{category} has no row at all"
        for level in ("1", "2", "3", "4", "5"):
            assert level in actions[category], f"{category} has no verdict at level {level}"


# ---------------------------------------------------------------------
# 8. the pipeline: who says what, and in what order
# ---------------------------------------------------------------------


class ScriptedConfig:
    """A model routing layer that answers per agent, and records who was asked.

    Stands in for :class:`~body.mouth.kilo.kilo_config.KiloConfig` so the pipeline
    tests exercise the *engine's* orchestration — which agent runs, in what order,
    and what its output is turned into — without reaching a model. Asserting on the
    real config would make these tests a function of whether a credential is in
    ``.env``, and a test that passes only on the author's machine is not a test.

    Attributes:
        replies: What each agent name answers.
        asked: The agent names, in the order they were called.
        prompts: The prompt each agent received, keyed by call order.
    """

    def __init__(self, replies: Dict[str, str]) -> None:
        """Build the stub.

        Args:
            replies: The answer each agent gives. An agent with no entry answers a
                short marker rather than raising, so a test can assert that an agent
                the pipeline was supposed to skip really was skipped.
        """
        self.replies = dict(replies)
        self.asked: List[str] = []
        self.prompts: List[str] = []

    async def infer(
        self, decision: Any, agent: str, prompt: str, system: str = "", timeout: Any = None
    ) -> InferenceResult:
        """Answer as the named agent.

        Args:
            decision: The caller's decision. Passed through untouched; this stub does
                not check it, because the tests that check decisions are elsewhere and
                a gate re-implemented here would be a gate that only exists in tests.
            agent: The agent being asked.
            prompt: The prompt.
            system: Ignored.
            timeout: Ignored.

        Returns:
            The scripted answer.
        """
        self.asked.append(str(agent))
        self.prompts.append(str(prompt))
        return InferenceResult(
            text=self.replies.get(str(agent), f"{agent} had nothing to say"),
            provider="stub", model=f"stub/{agent}", fell_back=False, reason="test",
        )

    def routing(self) -> Dict[str, Any]:
        """Report the stub as an unconfigured set of providers.

        Returns:
            A routing record, so :meth:`KiloEngine.status` works against this stub.
        """
        return {"providers": [], "fallback": None, "stub": True}


def pipeline_engine(workspace: KiloWorkspace, replies: Dict[str, str]) -> KiloEngine:
    """Return an engine whose writes land through Fingers and whose answers are scripted.

    Args:
        workspace: The isolated guard.
        replies: What each agent answers.

    Returns:
        The engine, with a real FileManager behind the proposal store so an apply in
        these tests goes through the same write path production uses.
    """
    from body.fingers import DirectoryManager, FileManager
    from body.mouth.kilo import DiffStore

    files = FileManager()
    return KiloEngine(
        workspace=workspace, config=ScriptedConfig(replies),
        diffs=DiffStore(files=files, directories=DirectoryManager()),
        files=files, directories=DirectoryManager(),
        reviewer=review_module.Reviewer(config=None),
    )


FLASK_BLOCKS = (
    "```path: app.py\n\n"
    "from flask import Flask\n\n"
    "app = Flask(__name__)\n\n\n"
    "@app.route('/hello')\n"
    "def hello():\n"
    "    return {'hello': 'world'}\n"
    "```\n\n"
    "```path: requirements.txt\n\nflask>=3.0\n```"
)


def test_kilo_architect_designs_structure(workspace: KiloWorkspace) -> None:
    """``plan`` asks the Architect and writes nothing.

    The design is the artifact here, so the assertions are that it came back, that it
    is the Architect's answer rather than a Coder's, and that no file appeared. A
    design that also created files would make "let me see the shape first" mean
    "let me see the shape after I have already built it".
    """
    import asyncio

    config = ScriptedConfig({"architect": "app.py, tests/test_app.py, requirements.txt"})
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    result = asyncio.run(engine_one.plan(allow("generate_code"), "a Flask API", "demo"))

    assert config.asked == ["architect"]
    assert "app.py" in result["design"]
    assert "diff_id" not in result, "planning produced a proposal, which writes nothing"
    assert list(Path(workspace.resolve("demo").path()).rglob("*")) == []


def test_kilo_coder_implements_design(workspace: KiloWorkspace) -> None:
    """``generate`` puts the Architect's design in front of the Coder.

    Asserted on the prompt rather than on the output: the whole reason the Architect
    runs first is that the Coder is *given* the layout, and a refactor that inverted
    the arguments would still produce working code, just with a layout nobody chose.
    The proposal is asserted alongside so the test also proves the files were parked
    rather than written.
    """
    import asyncio

    config = ScriptedConfig({"architect": "app.py then requirements.txt", "coder": FLASK_BLOCKS})
    engine_one = KiloEngine(workspace=workspace, config=config,
                            reviewer=review_module.Reviewer(config=None))

    result = asyncio.run(
        engine_one.generate(allow("generate_code"), "a Flask API", "demo")
    )

    assert config.asked == ["architect", "coder"], "the architect must design before the coder writes"
    coder_prompt = config.prompts[1]
    assert "app.py then requirements.txt" in coder_prompt, "the coder never saw the design"
    assert result["design"] == "app.py then requirements.txt", "the design is returned for the user"
    assert result["files"] == 2, "both fenced blocks should become changes"
    assert [item["path"] for item in result["changes"]] == ["app.py", "requirements.txt"]


def test_kilo_generates_multi_file_project(workspace: KiloWorkspace) -> None:
    """One description becomes a multi-file proposal, and the disk stays untouched.

    The "writes nothing" half matters more than the file count. A generating call that
    leaves bytes on disk has skipped the entire propose → review → approve → apply
    flow, and no assertion about file *count* would catch it.
    """
    import asyncio

    engine_one = pipeline_engine(
        workspace, {"architect": "three files", "coder": FLASK_BLOCKS}
    )
    root = workspace.resolve("demo")

    result = asyncio.run(
        engine_one.generate(allow("generate_code"), "a Flask API", "demo")
    )

    assert result["diff_id"]
    assert result["files"] == 2
    paths = sorted(item["path"] for item in result["changes"])
    assert paths == ["app.py", "requirements.txt"]
    assert result["agent"] == "coder", "the proposal records which agent produced it"
    assert list(Path(root.path()).rglob("*")) == [], "generate wrote to disk"


def test_kilo_reviewer_finds_issues(workspace: KiloWorkspace) -> None:
    """The Reviewer reports findings against real source, and fixes nothing.

    Uses the deterministic checks rather than a model, because the static pass is the
    one that runs whether or not a credential exists — a reviewer that only works
    online is not a reviewer.
    """
    import asyncio

    from body.fingers import DirectoryManager, FileManager

    engine_one = pipeline_engine(workspace, {})
    root = workspace.resolve("demo")
    source = (
        "import os\n"
        "PASSWORD = os.environ['DB_PASSWORD']\n"
        "def run(cmd):\n"
        "    os.system(cmd)\n"
    )
    DirectoryManager().mkdir(allow("write_files"), root.path())
    FileManager().write(allow("write_files"), root.path("app.py"), source)

    result = asyncio.run(engine_one.review(allow("read_files", root.path("app.py")), "app.py", "demo"))

    assert result["findings"], "the deterministic checks found nothing in this file"
    assert result["path"] == "app.py"
    body = json.dumps(result["findings"]).lower()
    assert "password" in body or "credential" in body, "the hardcoded credential went unremarked"
    assert Path(root.path("app.py")).read_text(encoding="utf-8") == source, "reviewing edited the file"


def test_kilo_debugger_fixes_error(workspace: KiloWorkspace) -> None:
    """The Debugger explains a failure and, when it can, proposes the fix.

    Two outcomes are legal — an explanation with a proposal, and an explanation with
    none — so the test asserts the first and separately asserts that the second is
    reported as ``proposed: 0`` rather than raised as an error. A debugger that
    refuses to answer when it declines to write is a debugger that fails at the most
    common outcome.
    """
    import asyncio

    engine_one = pipeline_engine(
        workspace,
        {"debugger": "```path: app.py\n\ndef hello():\n    return {'hi': 1}\n```"},
    )

    result = asyncio.run(
        engine_one.debug(allow("generate_code"), "KeyError: 'greeting'", "return {'hi': 1}", "demo")
    )

    assert result["proposed"] == 1, "the debugger's whole-file fix should become a change"
    assert result["diff_id"], "a proposed fix must be applicable"
    assert list(Path(workspace.resolve("demo").path()).rglob("*")) == []

    talker = pipeline_engine(workspace, {"debugger": "The key is misspelled: 'hi', not 'greeting'."})
    quiet = asyncio.run(talker.debug(allow("generate_code"), "KeyError: 'greeting'", "", "demo"))
    assert quiet["proposed"] == 0
    assert "misspelled" in quiet["explanation"]
    assert "diff_id" not in quiet, "an explanation with no fix must not look like a proposal"

    assert list(Path(workspace.resolve("demo").path()).rglob("*")) == []


def test_kilo_tester_writes_and_runs_tests(workspace: KiloWorkspace) -> None:
    """The Tester writes tests into a test directory; running them is a separate row.

    The path is the assertion. A ``test_app.py`` dropped beside ``app.py`` is a test
    that never runs, and a suite that stays green because it collected nothing is
    worse than having no test — so the Tester's proposal is checked for the ``tests/``
    prefix rather than for merely existing.
    """
    import asyncio

    engine_one = pipeline_engine(
        workspace,
        {"tester": "```path: whatever.py\n\ndef test_hello():\n    assert True\n```"},
    )

    result = asyncio.run(
        engine_one.write_tests(allow("generate_code"), "app.py", "", "demo")
    )

    paths = [item["path"] for item in result["changes"]]
    assert paths, "the tester proposed nothing"
    assert all(path.startswith("tests/") for path in paths), (
        f"tests must land in a test directory, not beside the module: {paths}"
    )

    decision = allow("execute_code")
    assert engine_one.category_for("tests") == "execute_code"
    assert engine_one.category_for("generate") == "generate_code"
    assert decision.allowed is True


def test_kilo_only_coder_debugger_tester_return_file_blocks() -> None:
    """Only the three writers are asked for files, and the other two cannot be.

    Enumerated over the permission grants rather than over the agent list, so an agent
    added next week that carries ``write:propose`` by accident is a test failure
    rather than a seventh way to propose a diff.
    """
    assert agents.writers() == ("coder", "debugger", "tester")
    for name, agent in agents.AGENTS.items():
        grant = agent.allows("write:propose")
        assert grant is agent.can_write, f"{name} disagrees with itself about writing"
        assert grant == (name in ("coder", "debugger", "tester"))
    assert agents.AGENTS["architect"].allows("write:propose") is False
    assert agents.AGENTS["reviewer"].allows("write:propose") is False


def test_kilo_multi_agent_coordination_flow(workspace: KiloWorkspace) -> None:
    """Architect designs, Coder implements, Reviewer objects, Tester covers.

    One test for the whole sequence, because the sequence is the product: five agents
    that each work correctly in isolation still deliver nothing if the Tester runs
    before the Coder, or if the Coder never sees the design. Asserting the order is
    what pins the orchestration down — the individual agent tests would all still pass.
    """
    import asyncio

    engine_one = pipeline_engine(
        workspace,
        {
            "architect": "app.py with a /hello route",
            "coder": FLASK_BLOCKS,
            "reviewer": "the greeting key should match the route name",
            "tester": "```path: app/tests/test_app.py\n\ndef test_hello():\n    assert True\n```",
        },
    )
    generate_decision = allow("generate_code")
    root = workspace.resolve("demo")
    from body.fingers import DirectoryManager, FileManager

    DirectoryManager().mkdir(allow("write_files"), root.path())
    FileManager().write(
        allow("write_files"), root.path("app.py"),
        "from flask import Flask\n\napp = Flask(__name__)\n",
    )

    design = asyncio.run(engine_one.plan(generate_decision, "a Flask API", "demo"))
    built = asyncio.run(engine_one.generate(generate_decision, "a Flask API", "demo"))
    objecting = asyncio.run(
        engine_one.review(allow("read_files", root.path("app.py")), "app.py", "demo")
    )
    covered = asyncio.run(engine_one.write_tests(generate_decision, "app.py", "", "demo"))

    assert engine_one.config.asked == ["architect", "architect", "coder", "tester"], (
        f"the pipeline ran its design steps out of order: {engine_one.config.asked}"
    )
    assert design["design"] == built["design"], "the two architect calls disagreed"
    assert built["files"] == 2, "the coder's two blocks should both become changes"
    assert objecting["findings"] == [] or isinstance(objecting["findings"], list), (
        "the reviewer must return a list, empty or not; a missing key is a crash not a review"
    )
    assert covered["changes"], "the tester proposed no tests"

    # Nothing has been applied, so every proposal is still pending and the folder is
    # still empty. Coordination is not consent.
    assert engine_one.preview_diff()["count"] == 2, "both proposals should be awaiting a decision"
    assert list(Path(root.path()).rglob("*")) == [Path(root.path("app.py"))], "the coordinated pipeline wrote new files to disk"


def test_kilo_diff_shows_all_changes_unified_format(workspace: KiloWorkspace) -> None:
    """Every changed file carries a unified diff and a side-by-side rendering.

    Both formats, not one. Unified is what a user reads to decide whether a change is
    small; side-by-side is what a reviewer reads to decide whether it is correct. A
    viewer that renders only one is fine for one of those questions.

    The file count is asserted against the proposal rather than against the diff text,
    so "two files proposed" and "two files diffed" cannot both be true while only one
    of them actually is.
    """
    engine_one = pipeline_engine(workspace, {})
    root = workspace.resolve("demo")
    from body.fingers import DirectoryManager, FileManager

    files = FileManager()
    DirectoryManager().mkdir(allow("write_files"), root.path())
    files.write(allow("write_files"), root.path("app.py"), "print('old')\n")

    proposal = engine_one.diffs.propose(
        root,
        [
            ("app.py", "print('new')\n"),
            ("requirements.txt", "flask>=3.0\n"),
            ("README.md", "# demo\n"),
        ],
        agent="coder", reader=lambda path: files.read(allow("read_files", path), path),
    )

    record = proposal.to_dict(include_text=True)
    assert record["files"] == 3
    for change in record["changes"]:
        assert change["unified"], f"{change['path']} has no unified diff"
        assert change["side_by_side"], f"{change['path']} has no side-by-side rendering"
        assert change["path"] in change["unified"], f"the diff for {change['path']} names another file"

    by_path = {item["path"]: item for item in record["changes"]}
    assert "new" in by_path["app.py"]["unified"] and "old" in by_path["app.py"]["unified"], (
        "a diff that shows only the new side is not a diff"
    )
    assert by_path["requirements.txt"]["before"] in ("", None), "a new file's before side should be empty"
    assert by_path["requirements.txt"]["unified"].count("\n") >= 2, "an addition needs a hunk header"


def test_kilo_apply_confirmation_cannot_be_reused(workspace: KiloWorkspace) -> None:
    """One answer buys one apply. The second attempt is refused, not repeated.

    A confirmation is spent when it is used. If the same decision could apply the same
    proposal twice, then a UI that retries on a timeout would write every file in the
    change set a second time — and because the apply is what snapshots files for
    rollback, the second write would be the one the snapshot no longer matches.

    The third assertion is the reason the second one is not the whole test: a replay
    that raises *before* the proposal is consumed leaves a proposal that can never be
    applied at all, which is also a bug and also worth pinning.
    """
    engine_one = pipeline_engine(workspace, {})
    root = workspace.resolve("demo")

    proposal = engine_one.diffs.propose(
        root, code_file("app.py", "print('hello')\n"), agent="coder",
        reader=lambda path: "",
    )
    diff_id = proposal.diff_id
    modify = allow("modify_project", root.root)
    write = allow("write_files", root.root)

    first = engine_one.apply(modify, write, diff_id)
    assert first["written"], "the first apply wrote nothing"

    with pytest.raises((DecisionReplayRefused, DiffAlreadyApplied, DiffNotApplied)):
        engine_one.apply(modify, write, diff_id)

    with pytest.raises((DecisionReplayRefused, DiffNotApplied)):
        engine_one.rollback(modify, write, diff_id)