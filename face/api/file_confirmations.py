"""Face API — file operation confirmations.

The 202 flow: when the gate answers ``ask``, the route
does not execute and does not refuse. It parks the
action, its decision and a preview of what would be
affected, hands back a ``confirmation_id``, and waits
for the user's answer.

The registry is deliberately in-memory and capped at
the last ten operations: a confirmation is a prompt,
not a contract, and a server restart clearing pending
prompts is the safe direction — stale approvals must
not survive a restart.

The preview is what the confirmation popup shows:
operation, target, file count, total size, whether it
is reversible (trash or backup), and the Amygdala's
score. Computing it performs no writes and follows no
symlinks.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import Action
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.amygdala.threat_assessment import ThreatAssessment
from core.paths import PROTECTED, classify, normalise

from body.fingers.file_classifier import (
    get_size_info,
    is_secret_file,
)

#: How many pending confirmations are kept.
MAX_PENDING = 10

#: Upper bound on files counted for one preview.
PREVIEW_CAP = 10_000


class PendingConfirmations:
    """The last few operations awaiting a user's answer."""

    def __init__(self, limit: int = MAX_PENDING) -> None:
        """Create the registry.

        Args:
            limit: How many pending confirmations to keep.
        """
        self._limit = limit
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._order: list = []
        self._lock = threading.Lock()
        self._counter = 0

    def create(
        self,
        action: Action,
        decision: PermissionDecision,
        preview: Dict[str, Any],
    ) -> str:
        """Park an action awaiting confirmation.

        Args:
            action: The proposed action.
            decision: The gate's ``ask`` verdict.
            preview: What the operation would affect.

        Returns:
            The ``confirmation_id`` the user is shown.
        """
        with self._lock:
            self._counter += 1
            confirmation_id = f"conf-{int(time.time())}-{self._counter}"
            self._entries[confirmation_id] = {
                "action": action,
                "decision": decision,
                "preview": preview,
                "created": time.time(),
            }
            self._order.append(confirmation_id)
            while len(self._order) > self._limit:
                self._entries.pop(self._order.pop(0), None)
        return confirmation_id

    def pop(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        """Take back a parked confirmation.

        Args:
            confirmation_id: The id the user is answering.

        Returns:
            The parked entry, or None when unknown or expired.
        """
        with self._lock:
            entry = self._entries.pop(confirmation_id, None)
            if confirmation_id in self._order:
                self._order.remove(confirmation_id)
        return entry

    def __len__(self) -> int:
        return len(self._order)


def build_preview(
    operation: str, target: str, trash_stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Estimate what an operation would affect.

    Args:
        operation: The operation name, e.g. ``delete``.
        target: The action's target path.
        trash_stats: ``TrashManager.trash_stats()`` output,
            needed only for ``empty-trash``.

    Returns:
        File count, total size, human-readable size and
        whether the operation is reversible.
    """
    path = normalise(target)
    if operation in ("read", "stat"):
        info = get_size_info(path)
        return _preview(1, info["bytes"], reversible=False)
    if operation in ("write", "copy", "move", "rename"):
        info = get_size_info(path)
        return _preview(1, info["bytes"], reversible=True)
    if operation == "mkdir":
        return _preview(1, 0, reversible=False)
    if operation in ("list", "tree"):
        if os.path.isdir(path):
            try:
                return _preview(len(os.listdir(path)), 0, reversible=False)
            except OSError:
                pass
        return _preview(None, None, reversible=False)
    if operation in ("delete", "trash"):
        count, total, _ = _walk_totals(path)
        return _preview(count, total, reversible=True)
    if operation == "empty-trash":
        stats = trash_stats or {}
        return _preview(
            stats.get("count"), stats.get("bytes"), reversible=True
        )
    if operation == "restore":
        return _preview(1, None, reversible=True)
    if operation in ("search",):
        return _preview(None, None, reversible=False)
    return _preview(None, None, reversible=False)


def confirmation_payload(
    confirmation_id: str,
    operation: str,
    decision: PermissionDecision,
    threat: ThreatAssessment,
    preview: Dict[str, Any],
) -> Dict[str, Any]:
    """Build exactly what the confirmation popup renders.

    Args:
        confirmation_id: The id the answer must quote.
        operation: The operation name.
        decision: The gate's ``ask`` verdict.
        threat: The Amygdala's assessment.
        preview: The affected-files estimate.

    Returns:
        Operation, target, file count, total size, threat
        score, required trust level, reversibility and the
        dry-run option.
    """
    return {
        "confirmation_id": confirmation_id,
        "operation": operation,
        "target": decision.action.target,
        "file_count": preview.get("file_count"),
        "total_size_bytes": preview.get("total_bytes"),
        "total_size_human": preview.get("total_human"),
        "reversible": preview.get("reversible"),
        "threat": {
            "score": threat.score,
            "level": threat.level,
            "explanation": threat.explanation,
        },
        "trust_level": decision.trust_level,
        "decision": decision.decision,
        "reason": decision.reason,
        "dry_run_available": True,
    }


def _preview(
    file_count: Optional[int], total_bytes: Optional[int], reversible: bool
) -> Dict[str, Any]:
    """Assemble a preview dict."""
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "total_human": (
            None
            if total_bytes is None
            else _human(total_bytes)
        ),
        "reversible": reversible,
    }


def _human(size: int) -> str:
    """Render a byte count for a human."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} TB"


def _walk_totals(root: str):
    """Count files and bytes under a root, bounded.

    Protected trees are pruned and secrets are skipped, so
    a preview never counts — or reveals — system or
    credential files.
    """
    if not os.path.isdir(root):
        if os.path.isfile(root) and not is_secret_file(root):
            return 1, os.stat(root).st_size, False
        return 0, 0, False
    count = 0
    total = 0
    for directory, dirnames, filenames in os.walk(root):
        if classify(directory) == PROTECTED:
            dirnames.clear()
            continue
        for name in filenames:
            candidate = os.path.join(directory, name)
            if is_secret_file(candidate):
                continue
            try:
                total += os.stat(candidate).st_size
                count += 1
            except OSError:
                continue
            if count >= PREVIEW_CAP:
                return count, total, True
    return count, total, False
