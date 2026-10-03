"""Body/Fingers — directory operations behind the gate.

Make a directory, list one, search a tree, render a
tree. Same rule as every other Body method: a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
comes first, and the guards run before a single
directory entry is read.

Listing rules:

* hidden files (``.git``, ``.vscode``) are excluded
  unless ``include_hidden`` is set;
* secret files and directories are *never* listed,
  with or without ``include_hidden`` — there is no
  way to reach ``.env`` or ``.ssh`` through a listing;
* protected system trees are pruned from searches
  rather than descended into.

Search patterns are glob or regex, always treated as
data — a pattern is never executed.
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.fingers import guards
from body.fingers.file_classifier import (
    classify_path,
    is_secret_file,
    resolve_absolute,
)
from core.paths import PROTECTED, classify

#: Default page size for listings.
DEFAULT_LIMIT = 100

#: Deepest a search or tree will descend.
DEFAULT_MAX_DEPTH = 10


class DirectoryManager:
    """Directory operations. Every method requires a decision."""

    def __init__(
        self,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Wire the directory manager to its collaborators.

        Args:
            logger: Activity logger; None means the shared one.
            kill_switch: The switch re-checked on every call;
                None means the process-wide one.
        """
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()

    def mkdir(
        self,
        decision: PermissionDecision,
        path: str,
        parents: bool = False,
    ) -> Dict[str, Any]:
        """Create a directory.

        Args:
            decision: An allowed ``write_files`` decision.
            path: The directory to create.
            parents: Create missing parents too.

        Returns:
            The created path.

        Raises:
            FileExistsError: When the directory already exists.
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("mkdir"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_write(real)
        if os.path.exists(real):
            raise FileExistsError(f"{real} already exists")
        if parents:
            os.makedirs(real, exist_ok=True)
        else:
            os.mkdir(real)
        self._log("make_directory", path=real, parents=parents)
        return {"path": real}

    def list(
        self,
        decision: PermissionDecision,
        path: str,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        include_hidden: bool = False,
    ) -> Dict[str, Any]:
        """List a directory, one page at a time.

        Args:
            decision: An allowed ``read_files`` decision.
            path: The directory to list.
            limit: Page size.
            offset: Page start.
            include_hidden: Include hidden files; secrets are
                still excluded.

        Returns:
            The page of entries plus the total, so the UI can
            paginate.
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("list"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_listing(real)
        if not os.path.isdir(real):
            raise ValueError(f"{real} is not a directory")
        entries = [
            self._entry(os.path.join(real, name))
            for name in sorted(os.listdir(real))
            if _visible(name, include_hidden)
            and not is_secret_file(os.path.join(real, name))
        ]
        total = len(entries)
        page = entries[offset : offset + max(1, limit)]
        return {
            "path": real,
            "entries": page,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def search(
        self,
        decision: PermissionDecision,
        pattern: str,
        root: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
        include_hidden: bool = False,
        regex: bool = False,
    ) -> Dict[str, Any]:
        """Search a tree for files matching a pattern.

        Args:
            decision: An allowed ``read_files`` decision.
            pattern: A glob such as ``*.py``, or a regular
                expression when ``regex`` is set.
            root: Where to search; protected trees are pruned.
            max_depth: Deepest descent.
            include_hidden: Include hidden files; secrets are
                still excluded.
            regex: Treat ``pattern`` as a regular expression.

        Returns:
            The matches and how many were found.
        """
        resolved, _ = resolve_absolute(root)
        guards.verify_decision(
            decision, guards.category_for("search"), resolved, self.kill_switch
        )
        base = guards.resolve_target(root, self.logger)
        guards.guard_listing(base)
        if not os.path.isdir(base):
            raise ValueError(f"{base} is not a directory")
        matcher = _compile(pattern, regex)
        matches: List[Dict[str, Any]] = []
        for directory, depth in _walk(base, int(max_depth), include_hidden):
            for name in sorted(os.listdir(directory)):
                candidate = os.path.join(directory, name)
                if not _visible(name, include_hidden):
                    continue
                if is_secret_file(candidate):
                    continue
                if matcher(name) and os.path.isfile(candidate):
                    matches.append(self._entry(candidate))
        return {"pattern": pattern, "root": base, "matches": matches,
                "count": len(matches)}

    def tree(
        self,
        decision: PermissionDecision,
        path: str,
        max_depth: int = 3,
    ) -> Dict[str, Any]:
        """Render a directory as a nested dict.

        Args:
            decision: An allowed ``read_files`` decision.
            path: The directory to render.
            max_depth: Deepest descent.

        Returns:
            A nested node: name, type, classification and
            children.
        """
        resolved, _ = resolve_absolute(path)
        guards.verify_decision(
            decision, guards.category_for("tree"), resolved, self.kill_switch
        )
        real = guards.resolve_target(path, self.logger)
        guards.guard_listing(real)
        if not os.path.isdir(real):
            raise ValueError(f"{real} is not a directory")
        return self._node(real, int(max_depth))

    # -- internals ------------------------------------------------------

    def _entry(self, path: str) -> Dict[str, Any]:
        """Describe one directory entry."""
        is_dir = os.path.isdir(path)
        return {
            "name": os.path.basename(path),
            "path": path,
            "is_dir": is_dir,
            "size": 0 if is_dir else _safe_size(path),
            "modified": _safe_mtime(path),
            "classification": classify_path(path),
        }

    def _node(self, path: str, depth: int) -> Dict[str, Any]:
        """Build one tree node, recursing while depth allows."""
        node = self._entry(path)
        node["type"] = "dir" if node["is_dir"] else "file"
        node["children"] = []
        if depth <= 0 or not node["is_dir"]:
            return node
        for name in sorted(os.listdir(path)):
            if not _visible(name, False) or is_secret_file(
                os.path.join(path, name)
            ):
                continue
            child = os.path.join(path, name)
            if classify(child) == PROTECTED:
                continue
            node["children"].append(self._node(child, depth - 1))
        return node

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired."""
        if self.logger is not None:
            self.logger.log("body", action, **fields)


def _visible(name: str, include_hidden: bool) -> bool:
    """Return True when a name may be shown."""
    return include_hidden or not name.startswith(".")


def _compile(pattern: str, regex: bool):
    """Turn a pattern into a name matcher.

    The pattern is data: a glob is matched with ``fnmatch``
    and a regex with ``re.search`` — never executed.
    """
    if regex:
        compiled = re.compile(pattern)
        return compiled.search
    return lambda name: fnmatch.fnmatch(name, pattern)


def _walk(base: str, max_depth: int, include_hidden: bool = False):
    """Yield ``(directory, depth)`` pairs, pruning protected trees."""
    queue = [(base, 0)]
    while queue:
        directory, depth = queue.pop(0)
        if classify(directory) == PROTECTED:
            continue
        yield directory, depth
        if depth >= max_depth:
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not _visible(name, include_hidden) or is_secret_file(
                os.path.join(directory, name)
            ):
                continue
            child = os.path.join(directory, name)
            if os.path.isdir(child) and classify(child) != PROTECTED:
                queue.append((child, depth + 1))


def _safe_size(path: str) -> int:
    """Return a file's size, 0 when it cannot be stated."""
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _safe_mtime(path: str) -> Optional[str]:
    """Return a modification time as an ISO string, when known."""
    try:
        from datetime import datetime, timezone

        stamp = os.stat(path).st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
