"""Body/Mouth/Kilo — proposals, diffs, apply and rollback.

A proposed change is **text**. Nothing here writes until :meth:`DiffStore.apply` is
given an allowed ``modify_project`` decision, and that call goes through
:class:`~body.fingers.file_manager.FileManager` — so every applied byte is snapshotted
as ``.bak.1``, classified as a secret first, and refused if it lands in ZUZZ's own
tree.

That two-step shape is why the Coder is safe: it has no method that writes. The method
that writes belongs to a different object, under a different row, and needs a decision
the model never sees.

**Rollback is a real inverse.** ``before`` is captured at proposal time by reading
through Finger, and rollback writes that text back; a file the proposal created is
trashed and one it deleted is rewritten. Both directions are ``modify_project``,
because undoing an unattended write is the same exposure as making it.

The caller's ``write_files`` decision names the **workspace root** and is passed
straight through — :func:`body.fingers.guards._same_target` treats a directory
decision as covering everything beneath it, so one approval carries a whole change
set and this module never mints a decision of its own.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from body.mouth.kilo.exceptions import (
    DecisionRefused,
    DiffAlreadyApplied,
    DiffNotFound,
    DiffRefused,
    WorkspaceRefused,
)

__all__ = ["FileChange", "PendingDiff", "DiffStore", "parse_file_blocks", "get_diff_store"]

#: The fence the Coder is told to emit: ```` ```path:relative/file.py ````, the whole
#: file, then a closing fence.
_BLOCK = re.compile(
    r"^```+path:(?P<path>[^\r\n]+)\r?\n(?P<body>.*?)^```+\s*$", re.MULTILINE | re.DOTALL
)

#: Characters an agent must not produce in a path. Climbing out is caught by
#: :meth:`KiloWorkspace.path` too; this is the earlier check, on the name itself.
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._\-/\\ ]+$")

#: Bytes read when building a diff's "before" side.
MAX_DIFF_READ = 5_242_880


def parse_file_blocks(text: str) -> List[Tuple[str, str]]:
    """Extract ``(path, contents)`` pairs from an agent's reply.

    Args:
        text: The raw reply.

    Returns:
        One pair per ``path:`` block, in order. A reply with no blocks yields ``[]``,
        which is a real answer the engine reports as "described rather than produced".

    Raises:
        DiffRefused: A block names a path that climbs out or is not a plain path.
    """
    found: List[Tuple[str, str]] = []
    for match in _BLOCK.finditer(str(text or "")):
        raw = match.group("path").strip()
        path = raw.strip("/\\")
        if not path or ".." in path.replace("\\", "/").split("/"):
            raise DiffRefused(
                f"the reply named {raw!r}, which climbs out of the workspace. A "
                f"file path must be relative and stay inside it."
            )
        if not _SAFE_PATH.match(path):
            raise DiffRefused(f"the reply named {path!r}, which is not a plain file path")
        body = match.group("body")
        found.append((path, body if body.endswith("\n") else body + "\n"))
    return found


def build_change(path: str, before: Optional[str], after: Optional[str]) -> "FileChange":
    """Build one change from the old and new text, with both diff renderings.

    Args:
        path: Workspace-relative path.
        before: Current contents, or None when the file is new.
        after: Proposed contents, or None to delete the file.

    Returns:
        The change, with a unified patch, side-by-side rows and a digest.

    Raises:
        DiffRefused: The two texts are identical, so there is nothing to apply.
    """
    if before == after:
        raise DiffRefused(f"{path} is unchanged, so it is not part of the change set")
    old = (before or "").splitlines(keepends=True)
    new = (after or "").splitlines(keepends=True)
    name = str(path).replace("\\", "/")
    unified = "".join(
        difflib.unified_diff(old, new, fromfile=f"a/{name}", tofile=f"b/{name}", n=3)
    )
    rows: List[List[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, [l.rstrip("\n") for l in old], [l.rstrip("\n") for l in new]
    ).get_opcodes():
        rows += [["-", l.rstrip("\n")] for l in old[i1:i2]]
        rows += [["+", l.rstrip("\n")] for l in new[j1:j2]]
    return FileChange(
        path=str(path), before=before, after=after, unified=unified, side_by_side=rows,
        digest=hashlib.sha256((after or "").encode("utf-8")).hexdigest(),
    )


def _count(unified: str, marker: str) -> int:
    """Count added or removed lines, excluding the ``+++``/``---`` file headers.

    Counting the header makes a one-line file report two additions, and a diff whose
    totals are wrong is a diff the user approves without reading.
    """
    return sum(
        1
        for line in unified.splitlines()
        if line.startswith(marker) and not line.startswith(marker * 3)
    )


@dataclass(frozen=True)
class FileChange:
    """One file's proposed new contents.

    Attributes:
        path: Workspace-relative path.
        before: What the file holds now, or None when it does not exist.
        after: What it would hold. None means "delete this file".
        unified: The unified diff, ready for ``git apply``.
        side_by_side: Rows for the Code page.
        digest: SHA-256 of ``after``, so an apply can prove it wrote what was
            previewed rather than something that changed in between.
    """

    path: str
    before: Optional[str]
    after: Optional[str]
    unified: str
    side_by_side: List[List[str]]
    digest: str

    @property
    def status(self) -> str:
        """Return ``added``, ``deleted`` or ``modified``."""
        if self.before is None:
            return "added"
        return "deleted" if self.after is None else "modified"

    @property
    def additions(self) -> int:
        """Return how many lines this change adds."""
        return _count(self.unified, "+")

    @property
    def deletions(self) -> int:
        """Return how many lines this change removes."""
        return _count(self.unified, "-")

    def to_dict(self, include_text: bool = True) -> Dict[str, Any]:
        """Return the change as plain data, optionally without the file bodies.

        Args:
            include_text: Include ``before``, ``after`` and the side-by-side rows.
                False for a list view, which is what the workspace endpoint returns.
        """
        record: Dict[str, Any] = {
            "path": self.path, "status": self.status, "additions": self.additions,
            "deletions": self.deletions, "unified": self.unified, "digest": self.digest,
        }
        if include_text:
            record.update(before=self.before, after=self.after,
                          side_by_side=self.side_by_side)
        return record


@dataclass
class PendingDiff:
    """A proposed change set waiting for a person.

    Attributes:
        diff_id: Short identifier the user approves by.
        workspace: The absolute workspace root.
        changes: The :class:`FileChange` records.
        agent: Which agent proposed it.
        created: Unix timestamp.
        applied: When it was written, or 0.
        rolled_back: When it was undone, or 0.
    """

    diff_id: str
    workspace: str
    changes: List[FileChange]
    agent: str = ""
    created: float = field(default_factory=time.time)
    applied: float = 0.0
    rolled_back: float = 0.0

    @property
    def pending(self) -> bool:
        """Return True while nothing has been applied or undone."""
        return not self.applied and not self.rolled_back

    def to_dict(self, include_text: bool = False) -> Dict[str, Any]:
        """Return the proposal as plain data for the API and the log.

        Args:
            include_text: Include each file's before/after bodies.
        """
        return {
            "diff_id": self.diff_id, "workspace": self.workspace, "agent": self.agent,
            "created": self.created, "applied": self.applied,
            "rolled_back": self.rolled_back, "pending": self.pending,
            "files": len(self.changes),
            "additions": sum(c.additions for c in self.changes),
            "deletions": sum(c.deletions for c in self.changes),
            "changes": [c.to_dict(include_text) for c in self.changes],
        }


def read_plain(path: str) -> Optional[str]:
    """Read a file's text for diffing, tolerating encoding and size.

    Bounded at :data:`MAX_DIFF_READ` so a large binary in a project cannot be pulled
    into memory by a preview.

    Args:
        path: The file to read.

    Returns:
        The text, or None when it is missing, too large or unreadable.
    """
    try:
        if os.path.getsize(path) > MAX_DIFF_READ:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


class DiffStore:
    """Pending proposals, and the only path in this package that writes one."""

    def __init__(
        self,
        files: Optional[Any] = None,
        directories: Optional[Any] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        """Wire the store to the Body layer that will do the writing.

        Args:
            files: The :class:`~body.fingers.file_manager.FileManager` every write
                goes through. Required for :meth:`apply`; the store refuses rather
                than falling back to ``open()``.
            directories: The DirectoryManager used to create a file's parent.
            logger: Activity logger.
            kill_switch: The emergency stop.
        """
        self.files = files
        self.directories = directories
        self.logger = logger
        self.kill_switch = kill_switch
        self._pending: Dict[str, PendingDiff] = {}

    # -- proposing -------------------------------------------------------

    def propose(
        self,
        workspace: Any,
        blocks: List[Tuple[str, str]],
        agent: str = "",
        reader: Optional[Any] = None,
    ) -> PendingDiff:
        """Build a proposal from an agent's file blocks. Not gated; a preview.

        Capturing the current contents is part of building the diff, and it goes
        through ``reader`` — a ``read_files`` decision the caller holds — rather than
        through :func:`open`. A proposal that guessed the "before" text would produce
        a rollback that restores a fiction.

        Args:
            workspace: The :class:`~body.mouth.kilo.kilo_workspace.Workspace`.
            blocks: ``(path, contents)`` pairs from :func:`parse_file_blocks`.
            agent: Which agent proposed them, for the log.
            reader: ``(path) -> text`` callable. None treats every file as new, which
                is correct only for a workspace the caller knows is empty.

        Returns:
            The proposal, parked until it is applied or dropped.

        Raises:
            DiffRefused: No usable blocks, or one path appears twice.
        """
        if not blocks:
            raise DiffRefused(
                "the agent described the change instead of producing files; there "
                "is nothing to preview"
            )
        changes: List[FileChange] = []
        seen: set = set()
        for path, contents in blocks:
            if path in seen:
                raise DiffRefused(f"{path} appears twice in one proposal")
            seen.add(path)
            absolute = workspace.path(path)
            before = reader(absolute) if (reader and os.path.exists(absolute)) else (
                read_plain(absolute) if os.path.exists(absolute) else None
            )
            changes.append(build_change(path, before, contents))
        proposal = PendingDiff(uuid.uuid4().hex[:12], workspace.root, changes, agent)
        self._pending[proposal.diff_id] = proposal
        self._log("kilo_diff_proposed", diff_id=proposal.diff_id,
                  files=len(changes), agent=agent)
        return proposal

    # -- reading proposals -----------------------------------------------

    def get(self, diff_id: str) -> PendingDiff:
        """Return one parked proposal. Not gated; a gauge.

        Args:
            diff_id: The proposal identifier.

        Returns:
            The proposal.

        Raises:
            DiffNotFound: No such proposal. In-memory on purpose — a restart must not
                leave an approval waiting on disk for something nobody saw.
        """
        found = self._pending.get(str(diff_id))
        if found is None:
            raise DiffNotFound(
                f"{diff_id!r} is not a pending change set. Proposals live in memory "
                f"only, so a restart clears them."
            )
        return found

    def preview(self, diff_id: str = "") -> Dict[str, Any]:
        """Return what is waiting to be applied. Not gated; a gauge.

        This is the panel the user reads *in order to decide*, so it asks for
        nothing — a gauge behind a confirmation is a gauge nobody looked at.

        Args:
            diff_id: One proposal, or ``""`` for all of them.
        """
        if diff_id:
            return self.get(diff_id).to_dict(include_text=True)
        return {"count": len(self._pending),
                "diffs": [item.to_dict() for item in self._pending.values()]}

    def drop(self, diff_id: str) -> bool:
        """Discard a proposal without applying it. Not gated; nothing happened.

        Args:
            diff_id: The proposal to discard.

        Returns:
            True when there was one to discard. Rejecting needs no permission: a
            user who says no has already exercised the authority that matters.
        """
        return self._pending.pop(str(diff_id), None) is not None

    # -- writing ---------------------------------------------------------

    def apply(
        self, decision: Any, write_decision: Any, diff_id: str, force: bool = False
    ) -> Dict[str, Any]:
        """Write a proposal through Body/Fingers.

        Args:
            decision: An allowed ``modify_project`` decision.
            write_decision: An allowed ``write_files`` decision covering the
                workspace root.
            diff_id: The proposal to apply.
            force: Apply even when a file no longer matches the previewed contents.
                False by default, because overwriting a file the user edited since
                the preview is the one way a diff viewer causes data loss.

        Returns:
            What was written, and when.

        Raises:
            DecisionRefused: Either decision is missing or does not allow this.
            DiffNotFound: No such proposal.
            DiffAlreadyApplied: Already written.
            DiffRefused: A file changed since the preview and ``force`` is False.
        """
        self._verify(decision, "modify_project", write_decision)
        proposal = self.get(diff_id)
        if proposal.applied:
            raise DiffAlreadyApplied(
                f"{diff_id} was already applied at {proposal.applied}; use rollback"
            )
        written: List[Dict[str, Any]] = []
        for change in proposal.changes:
            absolute = self._target(proposal.workspace, change.path)
            if not force and read_plain(absolute) != change.before:
                raise DiffRefused(
                    f"{change.path} has changed since this diff was previewed. "
                    f"Re-run the proposal, or apply with force to overwrite it."
                )
            written.append(self._write(write_decision, absolute, change.after))
        proposal.applied = time.time()
        self._log("kilo_diff_applied", diff_id=diff_id, files=len(written))
        return {"diff_id": diff_id, "written": written, "applied": proposal.applied}

    def rollback(self, decision: Any, write_decision: Any, diff_id: str) -> Dict[str, Any]:
        """Undo a proposal through Body/Fingers.

        Args:
            decision: An allowed ``modify_project`` decision.
            write_decision: An allowed ``write_files`` decision covering the root.
            diff_id: The proposal to undo.

        Returns:
            What was restored and what was trashed.

        Raises:
            DecisionRefused: Either decision is missing or does not allow this.
            DiffAlreadyApplied: This proposal was never applied.
        """
        self._verify(decision, "modify_project", write_decision)
        proposal = self.get(diff_id)
        if not proposal.applied:
            raise DiffAlreadyApplied(
                f"{diff_id} was never applied, so there is nothing to roll back"
            )
        restored: List[Dict[str, Any]] = []
        for change in proposal.changes:
            absolute = self._target(proposal.workspace, change.path)
            if change.before is None:
                if os.path.exists(absolute):
                    restored.append({"path": absolute, "action": "trashed",
                                     **self.files.delete(write_decision, absolute)})
            else:
                restored.append({"path": absolute, "action": "restored",
                                 **self._write(write_decision, absolute, change.before)})
        proposal.rolled_back = time.time()
        self._log("kilo_diff_rolled_back", diff_id=diff_id, files=len(restored))
        return {"diff_id": diff_id, "restored": restored}

    # -- internals -------------------------------------------------------

    def _write(self, decision: Any, absolute: str, contents: Optional[str]) -> Dict[str, Any]:
        """Write or trash one file, creating its parent if needed.

        Args:
            decision: The allowed ``write_files`` decision, scoped to the root.
            absolute: The absolute path.
            contents: New text, or None to move the file to trash.

        Returns:
            The Finger record.

        Raises:
            DiffRefused: The store has no file manager, so it will not write. There
                is deliberately no ``open()`` fallback anywhere in this package.
        """
        if self.files is None:
            raise DiffRefused(
                "this DiffStore has no FileManager, so it will not write. Wire the "
                "Body's file layer before applying a proposal."
            )
        if contents is None:
            if not os.path.exists(absolute):
                return {"path": absolute, "skipped": "already absent"}
            return self.files.delete(decision, absolute)
        parent = os.path.dirname(absolute)
        if self.directories is not None and parent and not os.path.isdir(parent):
            self.directories.mkdir(decision, parent)
        return self.files.write(decision, absolute, contents)

    def _target(self, workspace_root: str, relative: str) -> str:
        """Return the absolute path, refusing anything outside the workspace.

        Re-checked at write time rather than trusted from proposal time, because the
        workspace in a parked proposal is a string from an earlier turn.

        Args:
            workspace_root: The proposal's workspace.
            relative: The path from the proposal.

        Returns:
            The absolute path.

        Raises:
            WorkspaceRefused: The path is not inside the workspace.
        """
        base = os.path.normpath(str(workspace_root))
        candidate = os.path.normpath(os.path.join(base, str(relative)))
        if candidate != base and not candidate.startswith(base + os.sep):
            raise WorkspaceRefused(f"{relative!r} resolves outside the workspace {base}")
        return candidate

    def _verify(self, decision: Any, category: str, write_decision: Any) -> None:
        """Refuse to write without both decisions.

        Args:
            decision: The ``modify_project`` decision.
            category: The category it must carry.
            write_decision: The ``write_files`` decision.

        Raises:
            DecisionRefused: Either is missing or does not allow the write.
        """
        for candidate, expected in ((decision, category), (write_decision, "write_files")):
            if candidate is None:
                raise DecisionRefused(
                    f"applying a proposal needs a {expected} decision the caller "
                    f"already holds; the Body does not mint its own"
                )
            if not getattr(candidate, "allowed", False):
                raise DecisionRefused(
                    f"the {expected} decision is {getattr(candidate, 'decision', '?')}: "
                    f"{getattr(candidate, 'reason', '')}"
                )

    def _log(self, action: str, **fields: Any) -> None:
        """Record a proposal event in the activity log, when wired."""
        if self.logger is None:
            return
        try:
            self.logger.log("kilo", action, **fields)
        except Exception:
            pass


_STORE: Optional[DiffStore] = None


def get_diff_store(
    files: Optional[Any] = None,
    directories: Optional[Any] = None,
    logger: Optional[Any] = None,
    kill_switch: Optional[Any] = None,
) -> DiffStore:
    """Return the process-wide proposal store, building it on first use.

    Args:
        files: The Body's FileManager.
        directories: The Body's DirectoryManager.
        logger: Activity logger.
        kill_switch: The emergency stop.
    """
    global _STORE
    if _STORE is None:
        _STORE = DiffStore(files=files, directories=directories, logger=logger,
                           kill_switch=kill_switch)
    return _STORE
