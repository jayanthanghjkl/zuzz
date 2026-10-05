"""Body/Mouth/Kilo — git behind the gate.

**Read-only git is not ``git_operations``.** ``status``, ``diff``, ``log``, ``branch``,
``show``, ``remote`` and ``rev-parse`` are reads of the user's own history and map to
``read_files``, which auto-approves from level 2. Filing them under
``git_operations`` would mean asking about ``git status``, which is the
over-prompting failure ``visual_capture`` is careful to avoid, and it would hide the
mutating row's real cost.

**History rewriting is refused outright, at every trust level.** Force-push,
``reset --hard``, ``clean``, ``push``, ``rebase``, ``filter-branch`` and a
``--force``-style flag raise :class:`~body.mouth.kilo.exceptions.GitRefused`. That is
why the module has its own refusal rather than relying on ``git_operations``'s row: a
trust row says *how often to ask*, and "never, because unrecoverable" is not a shape a
row can express.

No ``shell=True`` anywhere, for the same reason as
:mod:`body.mouth.kilo.kilo_terminal`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from body.mouth.kilo.exceptions import DecisionRefused, GitRefused, KillSwitchRefused

__all__ = ["GitResult", "KiloGit", "get_git", "READ_ONLY", "REFUSED_SUBCOMMANDS"]

#: Subcommands that only read. Mapped to ``read_files``.
READ_ONLY = frozenset({"status", "diff", "log", "show", "branch", "remote", "rev-parse"})

#: Subcommands that can destroy work. Refused before the matrix is consulted, so no
#: trust level and no Amygdala score can reach them.
REFUSED_SUBCOMMANDS = frozenset({
    "clean", "filter-branch", "filter-repo", "push", "rebase", "reset",
})

#: Flags that turn a read into a write. ``git diff`` is on the read list;
#: ``git diff --output=`` is not a read.
_REFUSING_FLAGS = frozenset({"--mirror", "--force", "-f", "--hard", "--prune", "--delete"})

#: Seconds a git command gets.
DEFAULT_TIMEOUT = 60

#: Bytes of output kept.
MAX_CAPTURE = 1_000_000


@dataclass(frozen=True)
class GitResult:
    """What one git command returned.

    Attributes:
        argv: The argument vector, as run.
        returncode: The exit status.
        stdout: Captured output.
        stderr: Captured error output.
        seconds: How long it ran.
        category: Which trust row this command was judged under.
    """

    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    category: str

    @property
    def ok(self) -> bool:
        """Return True when git exited zero."""
        return self.returncode == 0

    def to_dict(self, include_output: bool = True) -> Dict[str, Any]:
        """Return the result as plain data.

        Args:
            include_output: Include stdout, stderr and stdout split into ``lines`` —
                every caller wants the list and none wants to split it again.
        """
        record: Dict[str, Any] = {
            "argv": list(self.argv), "returncode": self.returncode, "ok": self.ok,
            "seconds": round(self.seconds, 2), "category": self.category,
        }
        if include_output:
            record.update(stdout=self.stdout, stderr=self.stderr,
                          lines=[line for line in self.stdout.splitlines() if line.strip()])
        return record


class KiloGit:
    """Git for the coding agents, with the destructive half removed."""

    def __init__(self, logger: Optional[Any] = None, kill_switch: Optional[Any] = None) -> None:
        """Build the git layer.

        Args:
            logger: Activity logger.
            kill_switch: The emergency stop, checked before every mutating command.
        """
        self.logger = logger
        self.kill_switch = kill_switch

    # -- policy ----------------------------------------------------------

    def category_for(self, subcommand: str) -> str:
        """Return the trust row a subcommand is judged under.

        Args:
            subcommand: The git subcommand.

        Returns:
            ``read_files`` for a read, ``git_operations`` for anything else.

        Raises:
            GitRefused: The subcommand is destructive, so there is no row to return.
        """
        name = str(subcommand or "").strip().lower()
        if name in REFUSED_SUBCOMMANDS:
            raise GitRefused(
                f"git {name} is refused at every trust level. It can make commits "
                f"unreachable, and no trust setting should be able to say otherwise. "
                f"Commit the work and ask the user if they want this."
            )
        return "read_files" if name in READ_ONLY else "git_operations"

    def describe(self) -> Dict[str, Any]:
        """Return the git policy. Not gated; a gauge.

        A user being asked to approve a git command deserves to see this first.
        """
        return {"read_only": sorted(READ_ONLY), "refused": sorted(REFUSED_SUBCOMMANDS),
                "refused_flags": sorted(_REFUSING_FLAGS), "shell": False,
                "timeout_seconds": DEFAULT_TIMEOUT}

    def check(self, argv: Sequence[str]) -> List[str]:
        """Validate a git command without running it. Not gated; a preview.

        Args:
            argv: The argument vector, starting with ``git``.

        Returns:
            The vector as a clean list.

        Raises:
            GitRefused: The vector is empty, has no subcommand, names a destructive
                subcommand, or carries a flag that would make a read a write.
        """
        parts = [str(item) for item in argv if str(item).strip()]
        if len(parts) < 2:
            raise GitRefused("no git subcommand was given")
        self.category_for(parts[1])
        for part in parts[1:]:
            lowered = part.strip().lower()
            if lowered in _REFUSING_FLAGS or lowered.startswith("--force"):
                raise GitRefused(
                    f"{part!r} is refused at every trust level: it rewrites history "
                    f"or discards uncommitted work"
                )
        return parts

    # -- gauges ----------------------------------------------------------

    def status(self, cwd: str) -> Dict[str, Any]:
        """Return a workspace's git status. Not gated; a gauge.

        Reads only the repository's own metadata, and is the panel the user reads *in
        order to decide* whether to approve a change set. Unlike
        :meth:`~KiloGit.run` it cannot be turned into a write by a flag, because this
        method builds the argument vector itself.

        Args:
            cwd: The workspace root.

        Returns:
            The porcelain status, parsed into staged/unstaged/untracked lists.
        """
        result = self._run(["git", "status", "--porcelain", "--branch"], cwd, "read_files")
        branch, entries = "", []
        for line in result.stdout.splitlines():
            if line.startswith("##"):
                branch = line[2:].strip()
            elif len(line) >= 3:
                entries.append({
                    "staged": line[0] not in (" ", "?"), "unstaged": line[1] not in (" ", "?"),
                    "untracked": line.startswith("??"), "code": line[:2],
                    "path": line[3:].strip(),
                })
        return {"repository": cwd, "is_repository": result.ok, "branch": branch,
                "clean": not entries, "changed": len(entries), "entries": entries}

    def diff(self, cwd: str, staged: bool = False) -> Dict[str, Any]:
        """Return the working tree's diff. Not gated; a gauge.

        Args:
            cwd: The workspace root.
            staged: Diff the index against HEAD rather than the working tree against
                the index.

        Returns:
            The patch and the files it touches.
        """
        argv = ["git", "diff", "--cached"] if staged else ["git", "diff"]
        patch = self._run(argv, cwd, "read_files")
        names = self._run(argv + ["--name-only"], cwd, "read_files")
        return {"repository": cwd, "patch": patch.stdout[:200_000],
                "files": [line for line in names.stdout.splitlines() if line.strip()]}

    def log(self, cwd: str, limit: int = 20) -> Dict[str, Any]:
        """Return recent commits. Not gated; a gauge.

        Args:
            cwd: The workspace root.
            limit: How many commits, capped at 200.

        Returns:
            One record per commit: hash, author, date and subject.
        """
        count = max(1, min(int(limit or 20), 200))
        result = self._run(
            ["git", "log", f"-{count}", "--pretty=format:%h|%an|%ad|%s", "--date=short"],
            cwd, "read_files",
        )
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "author": parts[1], "date": parts[2],
                                "subject": parts[3]})
        return {"repository": cwd, "count": len(commits), "commits": commits}

    # -- gated -----------------------------------------------------------

    def run(self, decision: Any, argv: Sequence[str], cwd: str) -> GitResult:
        """Run a git command the user approved.

        Args:
            decision: An allowed decision whose category matches the subcommand.
            argv: The argument vector, starting with ``git``.
            cwd: The workspace root.

        Returns:
            What git returned. A non-zero exit is a result, not an exception:
            ``git status`` outside a repository is information, not a crash.

        Raises:
            DecisionRefused: No decision, one that does not allow this category, or one
                issued for a different category.
            KillSwitchRefused: The stop is engaged.
            GitRefused: The command is destructive or not runnable here.
        """
        if decision is None:
            raise DecisionRefused("running git needs a decision the caller already holds")
        parts = self.check(argv)
        category = self.category_for(parts[1])
        issued = str(getattr(getattr(decision, "action", None), "category", ""))
        if issued != category:
            raise DecisionRefused(
                f"this git command is judged as {category}, but the decision was for {issued or '?'}"
            )
        if not getattr(decision, "allowed", False):
            raise DecisionRefused(
                f"the decision is {getattr(decision, 'decision', 'refused')}: "
                f"{getattr(decision, 'reason', '')}"
            )
        if self.kill_switch is not None and self.kill_switch.is_killed():
            raise KillSwitchRefused("kill switch engaged; nothing will be run")
        return self._run(parts, cwd, category)

    # -- internals -------------------------------------------------------

    def _run(self, argv: Sequence[str], cwd: str, category: str) -> GitResult:
        """Run git with no shell and a timeout.

        Args:
            argv: The argument vector.
            cwd: The working directory.
            category: The row this was judged under, recorded in the result.
        """
        parts = [str(item) for item in argv]
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell
                parts, cwd=cwd if os.path.isdir(cwd) else None, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=DEFAULT_TIMEOUT, shell=False,
            )
            code, out, err = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired:
            code, out, err = 124, "", f"git did not finish within {DEFAULT_TIMEOUT}s"
        except OSError as exc:
            raise GitRefused(f"git could not be started: {exc}") from exc
        self._log("kilo_git", argv=parts, cwd=cwd, category=category, returncode=code)
        return GitResult(argv=parts, returncode=int(code), stdout=str(out)[:MAX_CAPTURE],
                         stderr=str(err)[:MAX_CAPTURE],
                         seconds=time.monotonic() - started, category=category)

    def _log(self, action: str, **fields: Any) -> None:
        """Record a git call in the activity log, when wired."""
        if self.logger is None:
            return
        try:
            self.logger.log("kilo", action, **fields)
        except Exception:
            pass


_GIT: Optional[KiloGit] = None


def get_git(logger: Optional[Any] = None, kill_switch: Optional[Any] = None) -> KiloGit:
    """Return the process-wide git layer, building it on first use.

    Args:
        logger: Activity logger, used the first time only.
        kill_switch: The emergency stop.
    """
    global _GIT
    if _GIT is None:
        _GIT = KiloGit(logger=logger, kill_switch=kill_switch)
    return _GIT
