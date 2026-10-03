"""Body/Fingers — the trash.

Deletion is the one file operation that cannot be undone by
an apology, so the Body never deletes: it moves. Every
``delete`` lands in a ``.zuzz_trash`` directory on the same
drive as the source, with a manifest entry that remembers
where it came from, so restore is a move back rather than a
prayer.

The manifest lives in the project's trash directory even
when the item itself lives on another drive's trash, so
there is exactly one record of what was trashed.

Permanent deletion — emptying the trash — goes through
``send2trash``, which hands the item to the operating
system's recycle bin. Nothing in the Body calls ``unlink``
or ``shutil.rmtree``: purging ZUZZ's trash is a second
safety net, not a cliff.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.fingers.exceptions import (
    DecisionRefused,
    RestoreConflict,
    TrashError,
)
from body.fingers.file_classifier import (
    is_secret_file,
    is_zuzz_own_file,
    resolve_absolute,
)
from core.config import ROOT

TRASH_DIR_NAME = ".zuzz_trash"
MANIFEST_NAME = "manifest.json"

#: Items younger than this are never auto-purged, whatever the
#: configuration says.
MIN_RETENTION_DAYS = 7

#: Age at which the scheduled purge removes items.
AUTO_PURGE_DAYS = 30

#: Warn the UI when the trash holds more than this.
WARN_GB = 1.0

#: Free disk space below which the oldest items are purged first.
MIN_FREE_GB = 5.0


class TrashManager:
    """Moves files into the trash and back.

    Attributes:
        trash_root: Fixed trash location, or None to use the
            same-drive rule.
        purge_transport: Callable that permanently removes a path.
            Defaults to ``send2trash.send2trash``.
        logger: Activity logger, used to record every trash and
            restore.
        kill_switch: The switch re-checked on every operation,
            or None for the process-wide one.
    """

    def __init__(
        self,
        trash_root: Optional[str] = None,
        purge_transport: Optional[Callable[[str], None]] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        """Bind the trash to a location and a purge transport.

        Args:
            trash_root: Override for tests; None means the same-drive
                rule with the project directory as fallback.
            purge_transport: Callable that permanently removes a path.
            logger: Activity logger override.
            kill_switch: The switch to consult. It must be carried
                explicitly rather than looked up per call: a
                ``FileManager`` given one switch and asked to delete
                must reach the same switch through the trash, or a
                stop would be enforced on one path and missed on
                another.
        """
        self.trash_root = Path(trash_root) if trash_root else None
        self.purge_transport = purge_transport or _default_transport
        self.logger = logger
        self.kill_switch = kill_switch
        self._lock = _Lock()

    # -- trash ----------------------------------------------------------

    def trash(
        self, decision: PermissionDecision, path: str
    ) -> str:
        """Move a file or directory into the trash.

        Args:
            decision: An allowed ``delete_files`` decision for ``path``.
            path: What to trash.

        Returns:
            The ``trash_id`` the item can be restored by.

        Raises:
            DecisionRefused: When the decision is missing, not allowed,
                or was issued for a different target.
            TrashError: When the item does not exist or cannot be moved.
        """
        from body.fingers.guards import guard_deletion

        resolved, _ = resolve_absolute(path)
        self._require(decision, "delete_files", resolved)
        guard_deletion(resolved)
        source = Path(resolved)
        if not source.exists():
            raise TrashError(f"nothing to trash at {resolved}")
        trash_id = uuid.uuid4().hex
        location = self._location_for(source)
        destination = location / trash_id / source.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as exc:
            raise TrashError(f"could not trash {resolved}: {exc}") from exc
        self._record(
            trash_id,
            original_path=resolved,
            size=_dir_size(source),
            location=str(location),
        )
        self._log("delete_file", path=resolved, trash_id=trash_id,
                  reversible=True)
        return trash_id

    # -- restore --------------------------------------------------------

    def restore(self, decision: PermissionDecision, trash_id: str) -> str:
        """Move a trashed item back where it came from.

        Args:
            decision: An allowed ``write_files`` decision.
            trash_id: The id ``trash`` returned.

        Returns:
            The original path.

        Raises:
            DecisionRefused: When the decision is missing or not allowed.
            TrashError: When the id is unknown.
            RestoreConflict: When the original path is occupied again.
        """
        self._require(decision, "write_files", f"trash:{trash_id}")
        entry = self._entry(trash_id)
        original = Path(entry["original_path"])
        if original.exists():
            raise RestoreConflict(
                f"{original} exists again; refusing to overwrite it"
            )
        item = self._item_path(trash_id, entry)
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(original))
        except OSError as exc:
            raise TrashError(f"could not restore {trash_id}: {exc}") from exc
        self._forget(trash_id)
        self._log("restore_file", path=str(original), trash_id=trash_id)
        return str(original)

    # -- inventory ------------------------------------------------------

    def list_trash(self) -> List[Dict[str, Any]]:
        """Return every trashed item, newest first.

        Read-only metadata about ZUZZ's own trash, so no permission
        decision is required — the same reason a file manager can
        show its recycle bin without asking.

        Returns:
            Manifest entries with ``trash_id``, ``original_path``,
            ``trashed_at``, ``size`` and ``age_days``.
        """
        entries = [dict(entry, trash_id=trash_id)
                   for trash_id, entry in self._manifest().items()]
        entries.sort(key=lambda entry: entry.get("trashed_at", 0), reverse=True)
        for entry in entries:
            entry["age_days"] = round(
                (time.time() - entry.get("trashed_at", 0)) / 86400, 2
            )
        return entries

    def trash_stats(self) -> Dict[str, Any]:
        """Return the trash's size, for the UI warning."""
        entries = self.list_trash()
        total = sum(int(entry.get("size", 0)) for entry in entries)
        return {
            "count": len(entries),
            "bytes": total,
            "warn": total > WARN_GB * 1024 ** 3,
        }

    # -- purge ----------------------------------------------------------

    def empty_trash(
        self, decision: PermissionDecision, older_than_days: float = 0
    ) -> int:
        """Permanently remove trashed items.

        The user asked for this, so the retention floor does not
        apply: ``older_than_days=0`` purges everything. Each purged
        item is handed to the purge transport — the operating
        system's recycle bin by default — not deleted outright.

        Args:
            decision: An allowed ``delete_files`` decision.
            older_than_days: Only purge items older than this.

        Returns:
            How many items were purged.
        """
        self._require(decision, "delete_files", "trash")
        return self._purge_where(
            lambda entry: _age_days(entry) > older_than_days
        )

    def auto_purge_old(self) -> int:
        """Scheduled purge: items older than 30 days, plus disk pressure.

        Runs from the scheduler, so it is a system job rather than an
        agent action and carries no decision. The 7-day retention
        floor always applies: no configuration can make the automatic
        purge touch a fresher item.

        Returns:
            How many items were purged.
        """
        threshold = max(AUTO_PURGE_DAYS, MIN_RETENTION_DAYS)
        purged = self._purge_where(
            lambda entry: _age_days(entry) > threshold
        )
        purged += self._purge_for_disk_space()
        return purged

    # -- internals ------------------------------------------------------

    def _purge_where(self, predicate: Callable[[Dict[str, Any]], bool]) -> int:
        """Purge manifest entries matching a predicate."""
        count = 0
        with self._lock:
            manifest = self._manifest()
            for trash_id in list(manifest):
                if not predicate(manifest[trash_id]):
                    continue
                if self._purge_item(trash_id, manifest[trash_id]):
                    count += 1
        return count

    def _purge_for_disk_space(self) -> int:
        """Purge oldest items until the drive has breathing room."""
        root = self._manifest_root()
        try:
            free_gb = shutil.disk_usage(str(root)).free / 1024 ** 3
        except OSError:
            return 0
        if free_gb >= MIN_FREE_GB:
            return 0
        count = 0
        entries = self.list_trash()
        entries.sort(key=lambda entry: entry.get("trashed_at", 0))
        for entry in entries:
            if free_gb >= MIN_FREE_GB:
                break
            if self._purge_item(entry["trash_id"], entry):
                count += 1
                free_gb += entry.get("size", 0) / 1024 ** 3
        return count

    def _purge_item(self, trash_id: str, entry: Dict[str, Any]) -> bool:
        """Hand one trashed item to the purge transport."""
        item = self._item_path(trash_id, entry)
        try:
            if item.exists():
                self.purge_transport(str(item))
            self._forget(trash_id)
        except OSError:
            return False
        self._log("delete_purged", trash_id=trash_id,
                  path=entry.get("original_path", ""))
        return True

    def _require(
        self, decision: PermissionDecision, category: str, target: str
    ) -> None:
        """Refuse unless the decision allows this operation here.

        Args:
            decision: What the caller was handed.
            category: The action category this method performs.
            target: The resolved path about to be acted on.

        Raises:
            DecisionRefused: The decision is missing, not allowed,
                was issued for another target, or predates an
                engaged kill switch.
        """
        from body.fingers.guards import verify_decision

        verify_decision(decision, category, target, self.kill_switch)

    def _location_for(self, source: Path) -> Path:
        """Return the trash directory for a source path.

        The same drive as the source keeps the move on one volume;
        when that is not possible — a read-only or foreign drive —
        the project directory's trash is the fallback.
        """
        if self.trash_root is not None:
            return self.trash_root
        drive = os.path.splitdrive(str(source))[0]
        if drive:
            candidate = Path(drive) / TRASH_DIR_NAME
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                pass
        return self._manifest_root()

    def _manifest_root(self) -> Path:
        """Return the directory holding the manifest."""
        if self.trash_root is not None:
            return self.trash_root
        return ROOT / TRASH_DIR_NAME

    def _manifest_path(self) -> Path:
        return self._manifest_root() / MANIFEST_NAME

    def _manifest(self) -> Dict[str, Dict[str, Any]]:
        path = self._manifest_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _record(self, trash_id: str, **fields: Any) -> None:
        with self._lock:
            manifest = self._manifest()
            manifest[trash_id] = {
                "trashed_at": time.time(), **fields
            }
            self._write_manifest(manifest)

    def _entry(self, trash_id: str) -> Dict[str, Any]:
        entry = self._manifest().get(trash_id)
        if entry is None:
            raise TrashError(f"unknown trash_id {trash_id}")
        return entry

    def _forget(self, trash_id: str) -> None:
        """Drop a manifest entry and the empty folder it occupied.

        Each item gets its own folder under the trash so two files of
        the same name cannot collide. Once the item has been restored
        or purged that folder is empty, and leaving it behind would
        accumulate one dead directory per operation for the lifetime of
        the installation. Removal is best-effort: a folder that is not
        empty, or that has already gone, is not an error worth raising
        over the operation that already succeeded.

        Args:
            trash_id: The id to forget.
        """
        with self._lock:
            manifest = self._manifest()
            entry = manifest.pop(trash_id, None)
            self._write_manifest(manifest)
        if entry is None:
            return
        folder = self._item_path(trash_id, entry).parent
        try:
            folder.rmdir()
        except OSError:
            pass

    def _write_manifest(self, manifest: Dict[str, Dict[str, Any]]) -> None:
        path = self._manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _item_path(self, trash_id: str, entry: Dict[str, Any]) -> Path:
        location = entry.get("location") or str(self._manifest_root())
        original = Path(entry["original_path"])
        return Path(location) / trash_id / original.name

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        The action names contain ``delete`` on purpose. The watchdog
        counts records whose action matches ``delete`` in order to
        detect mass deletion, and it reads this log rather than the
        filesystem. The Body is the only code that can delete, so if
        it logged these as ``trash_file`` a thousand deletions would
        stay invisible to the detector that exists to stop them.

        Args:
            action: The verb phrase, e.g. ``delete_file``.
            **fields: Structured detail for the record.
        """
        if self.logger is not None:
            self.logger.log("body", action, **fields)


def _default_transport(path: str) -> None:
    """Send a purged item to the operating system's recycle bin."""
    from send2trash import send2trash

    send2trash(path)


def _age_days(entry: Dict[str, Any]) -> float:
    """Return an entry's age in days."""
    return (time.time() - float(entry.get("trashed_at", 0))) / 86400


def _dir_size(path: Path) -> int:
    """Return the size of a file, or a directory's contents."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


class _Lock:
    """A tiny re-entrant lock wrapper, so the manager stays picklable."""

    def __init__(self) -> None:
        import threading

        self._inner = threading.RLock()

    def __enter__(self) -> "_Lock":
        self._inner.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._inner.__exit__(*exc)
