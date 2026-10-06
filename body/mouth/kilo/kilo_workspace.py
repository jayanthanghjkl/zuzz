"""Body/Mouth/Kilo — the workspace guard.

The refusal that makes this package safe. ZUZZ's coding agents may work in a project
folder; they may not work in ZUZZ.

**Why a refusal and not a permission level.** ``config/trust_levels.json`` can say *how
often to ask*. It cannot say *anywhere except here*. An ``auto`` row at level 5 would
authorise a write into ``brain/prefrontal_cortex/`` and into ``body/fingers/`` equally,
and the second of those is the code that decides what the first one is allowed to do.
So the block lives here, in code, where no trust level reaches it — which is also why
the path is never handed to the model in the first place: a path the model cannot see
is a path it cannot ask for.

**Why not a clamp either.** Silently relocating a write to somewhere "safe" is a write
somewhere the user did not approve, and a message saying so is how an assistant teaches
a user to stop reading its output.

Approved invariant 4 (Phase 4) is narrower than PROJECT.md §13, which permits modifying
ZUZZ's own plugins and non-core utility code. Self-improvement is Phase 7's business,
behind its own gate.

The default workspace root is ``temp/kilo/workspaces``: inside ZUZZ's tree, but inside
``temp/``, which :mod:`core.paths` already classifies disposable and the trash manager
owns. That is the "isolated scratch space" the decision names.

Nothing here writes. :meth:`KiloWorkspace.resolve` creates no directory, because a guard
that made directories would be a guard writing to disk without a decision — the thing
this whole package exists to avoid.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import ROOT
from core.paths import PROTECTED, classify, normalise

from body.mouth.kilo.exceptions import (
    WorkspaceConfirmationRequired,
    WorkspaceNotFound,
    WorkspaceRefused,
)

__all__ = ["CORE_DIRECTORIES", "CRITICAL_FILES", "Workspace", "KiloWorkspace", "get_workspace_guard"]

#: Directories the coding agents may never write to, read on the model's behalf, or
#: list. Deliberately broader than what is technically load-bearing: ``tests/`` and
#: ``scripts/`` are not safety-critical, but an agent that may rewrite the test suite
#: can prove its own work correct, and that is the failure mode rather than the file
#: that fails.
CORE_DIRECTORIES: Tuple[str, ...] = (
    "brain", "body", "face", "core", "config", "tests", "scripts", "docs",
    "storage", ".git",
)

#: Files refused even outside the directories above, named so that a refactor moving
#: them still has a row to match.
CRITICAL_FILES: Tuple[str, ...] = (
    "immutable_rules.py", "kill_switch.py", "activity_logger.py",
    "trust_levels.json", "PROJECT.md", "AGENTS.md",
)

#: Where a workspace lives when the caller names none.
DEFAULT_ROOT = Path(ROOT) / "temp" / "kilo" / "workspaces"

#: Largest listing returned, so a directory of build artefacts cannot become the prompt.
MAX_LISTED = 500


def is_inside(path: str, root: str) -> bool:
    """Return True when a path is a root or lies beneath it.

    Both sides are normalised, so ``C:\\Users\\me\\proj`` and ``C:/Users/me/proj/`` are
    the same directory and a sibling called ``proj-old`` is not inside ``proj``.

    Args:
        path: The candidate path.
        root: The root to test against.
    """
    target = normalise(os.path.abspath(str(path)))
    base = normalise(os.path.abspath(str(root)))
    return target == base or target.startswith(base + os.sep)


@dataclass(frozen=True)
class Workspace:
    """A resolved place Kilo is allowed to work.

    Attributes:
        root: The absolute, resolved root.
        name: Its directory name, for log lines and the UI.
        registered: Whether the user approved this root, as opposed to it merely
            being inside the default scratch root.
        scratch: Whether it lives under :data:`DEFAULT_ROOT`.
    """

    root: str
    name: str
    registered: bool = False
    scratch: bool = False

    def path(self, relative: str = "") -> str:
        """Return an absolute path inside this workspace.

        Args:
            relative: A path relative to the root. May not climb out.

        Returns:
            The absolute path.

        Raises:
            WorkspaceRefused: The relative path escapes the root.
        """
        if not str(relative).strip():
            return self.root
        candidate = os.path.normpath(os.path.join(self.root, str(relative)))
        if not is_inside(candidate, self.root):
            raise WorkspaceRefused(
                f"{relative!r} resolves to {candidate}, which is outside the "
                f"workspace {self.root}"
            )
        return candidate

    def to_dict(self) -> Dict[str, Any]:
        """Return the workspace as plain data for the API."""
        return {"root": self.root, "name": self.name, "registered": self.registered,
                "scratch": self.scratch, "exists": os.path.isdir(self.root)}


class KiloWorkspace:
    """Resolves and refuses workspaces."""

    def __init__(self, root: Optional[Any] = None, registered: Optional[List[str]] = None) -> None:
        """Build the guard.

        Args:
            root: Where workspaces live when the caller names none. Defaults to
                :data:`DEFAULT_ROOT`.
            registered: Extra roots the user has approved. These may be anywhere
                outside ZUZZ's core directories.
        """
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.registered: List[str] = [str(item) for item in (registered or [])]

    # -- policy ----------------------------------------------------------

    def is_refused(self, path: str) -> Tuple[bool, str]:
        """Return whether a path is refused, and which rule refused it.

        Separate from a bare boolean so the UI can tell a user *which* rule stopped a
        request rather than only that one did.

        Args:
            path: Any path, absolute or relative to the project root.

        Returns:
            ``(refused, reason)``. An empty reason means it is allowed.
        """
        resolved = self._absolute(path)
        if classify(resolved) == PROTECTED:
            return True, "protected system location"
        try:
            parts = Path(resolved).relative_to(Path(ROOT)).parts
        except ValueError:
            parts = ()
        if parts and parts[0] in CORE_DIRECTORIES:
            return True, f"ZUZZ's own {parts[0]}/ tree is read-only to the coding agents"
        if Path(resolved).name in CRITICAL_FILES:
            return True, f"{Path(resolved).name} is safety-critical"
        return False, ""

    def describe(self) -> Dict[str, Any]:
        """Return the workspace policy. Not gated; a gauge.

        This is the panel that answers "where is Kilo allowed to write", so it is
        deliberately ungated.
        """
        return {
            "scratch_root": str(self.root),
            "core_directories": list(CORE_DIRECTORIES),
            "critical_files": list(CRITICAL_FILES),
            "registered": list(self.registered),
            "project_root": str(ROOT),
        }

    def list_workspaces(self) -> List[Dict[str, Any]]:
        """List the workspaces that exist. Not gated; a gauge.

        Missing directories are reported rather than skipped, because a workspace
        that was configured and has since been deleted is worth seeing.
        """
        found: List[Dict[str, Any]] = []
        seen: set = set()
        if self.root.is_dir():
            for child in sorted(self.root.iterdir()):
                if child.is_dir():
                    seen.add(normalise(str(child)))
                    found.append(Workspace(str(child), child.name, True, True).to_dict())
        for path in self.registered:
            if normalise(path) not in seen:
                name = os.path.basename(str(path).rstrip("\\/")) or path
                found.append(Workspace(path, name, True, False).to_dict())
        return found

    def listing(self, workspace: Workspace, limit: int = 200) -> Dict[str, Any]:
        """Return a workspace's file tree. Not gated; a gauge.

        A model cannot reason about a directory it has not been shown, and the listing
        is ZUZZ's own view of the user's project — the manner of
        :meth:`body.legs.BrowserActions.list_tabs`.

        Args:
            workspace: The workspace to list.
            limit: How many entries to return.

        Returns:
            The entries, relative to the root, and how many were skipped.
        """
        entries: List[str] = []
        skipped = 0
        cap = max(1, min(int(limit or 200), MAX_LISTED))
        for base, dirs, files in os.walk(workspace.root):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", ".pytest_cache"}]
            for name in sorted(files):
                if len(entries) >= cap:
                    skipped += 1
                else:
                    entries.append(os.path.relpath(os.path.join(base, name),
                                                   workspace.root).replace("\\", "/"))
        return {"root": workspace.root, "entries": entries, "skipped": skipped}

    # -- resolution ------------------------------------------------------

    def resolve(self, name: str = "") -> Workspace:
        """Resolve a workspace name or path into a usable workspace. Creates nothing.

        Args:
            name: A directory name under the scratch root, or an absolute path.
                Empty means the scratch root itself.

        Returns:
            The workspace. :meth:`Workspace.to_dict` reports whether it exists yet.

        Raises:
            WorkspaceRefused: The target is ZUZZ's own code or a protected location.
            WorkspaceConfirmationRequired: The target is a plausible project folder
                the user has not approved yet. Carries the path, so the caller can ask
                about that folder specifically.
            WorkspaceNotFound: The target exists but is not a directory.
        """
        raw = str(name or "").strip()
        target = self._absolute(raw) if raw else str(self.root)

        refused, reason = self.is_refused(target)
        if refused:
            raise WorkspaceRefused(
                f"{target} cannot be a Kilo workspace: {reason}. The coding agents "
                f"work in project folders only, and ZUZZ's own tree is not one."
            )
        if not (is_inside(target, str(self.root)) or self._is_registered(target)):
            inside = "" if self._outside_project(target) else (
                "inside ZUZZ's tree but not in a core directory"
            )
            raise WorkspaceConfirmationRequired(
                f"{target} {inside or 'is not a workspace ZUZZ has been told about'}. "
                f"Approve this folder to let the coding agents work in it.",
                workspace=target,
            )
        if os.path.exists(target) and not os.path.isdir(target):
            raise WorkspaceNotFound(f"{target} exists but is not a directory")
        scratch = is_inside(target, str(self.root))
        return Workspace(target, os.path.basename(target.rstrip("\\/")) or "kilo", True, scratch)

    # -- approving -------------------------------------------------------

    def register(self, path: str) -> Workspace:
        """Approve a folder the user has just named, and return it.

        The counterpart to :class:`~body.mouth.kilo.exceptions.WorkspaceConfirmationRequired`,
        and the only way a path outside the scratch root ever becomes a workspace. It
        takes no decision because the caller's decision is the thing it is standing in
        for — which is why this method is not on the engine and not reachable from a
        tool: the approval has to come from a person answering a prompt, not from a
        model that decided the folder looked fine.

        Registering does not bypass the refusal. ``resolve`` still runs, so a path
        inside ZUZZ's own tree raises here exactly as it would have before.

        Args:
            path: The folder the user approved.

        Returns:
            The workspace, now registered.

        Raises:
            WorkspaceRefused: The path is inside ZUZZ's own tree.
            WorkspaceNotFound: The path is not a directory.
        """
        space = self.resolve(path)
        entry = os.path.normcase(os.path.normpath(space.root))
        if not any(
            entry == os.path.normcase(os.path.normpath(item)) for item in self.registered
        ):
            self.registered.append(str(space.root))
        return space

    # -- internals -------------------------------------------------------

    def _is_registered(self, path: str) -> bool:
        """Return True when a path is inside an approved root."""
        return any(is_inside(path, root) for root in self.registered)

    def _outside_project(self, path: str) -> bool:
        """Return True when the path is outside ZUZZ entirely."""
        try:
            Path(path).relative_to(Path(ROOT))
        except ValueError:
            return True
        return False

    def _absolute(self, path: str) -> str:
        """Return an absolute path, resolving a bare name against the scratch root.

        Symlinks are resolved here rather than at write time so the refusal is about
        the file that would actually be touched, following the one-level rule in
        :mod:`body.fingers.guards`.
        """
        text = str(path or "").strip()
        if not text:
            return str(self.root)
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = Path(self.root) / text
        return os.path.normpath(os.path.realpath(str(candidate)))


_GUARD: Optional[KiloWorkspace] = None


def get_workspace_guard(
    root: Optional[Any] = None, registered: Optional[List[str]] = None
) -> KiloWorkspace:
    """Return the process-wide workspace guard, building it on first use.

    Args:
        root: Override for the scratch root.
        registered: Extra approved roots.
    """
    global _GUARD
    if _GUARD is None:
        _GUARD = KiloWorkspace(root=root, registered=registered)
    return _GUARD
