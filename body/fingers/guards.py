"""Body/Fingers — shared guards.

Every Body method runs the same three checks before touching
the disk, in the same order, so no operation can forget one:

1. **The decision.** Is there a real
   :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`?
   Does it allow this operation? Was it issued for *this*
   target, or replayed from another path? Is the kill switch
   still disengaged? A decision issued before an emergency
   stop must not survive the stop.
2. **The real path.** Symlinks are resolved one level and the
   *target* is classified, because a link in the project
   directory pointing at ``C:\\Windows`` is a Windows file.
3. **The classification.** Secrets are blocked at every trust
   level; protected paths are blocked for reads, writes and
   deletes; ZUZZ's own code, logs and safety state are
   read-only.

The gate makes the same decisions, so this is defence in
depth: the gate is the boundary, the Body is the last line.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from brain.prefrontal_cortex.action import ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch
from core.paths import PROTECTED, classify, normalise

from body.fingers.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
    PathTraversalRefused,
    ProtectedPathRefused,
    SecretFileRefused,
    ZuzzCoreRefused,
)
from body.fingers.file_classifier import (
    escapes_project,
    is_secret_file,
    is_symlink,
    is_zuzz_own_file,
    resolve_absolute,
    resolve_symlink,
)


def verify_decision(
    decision: Any,
    category: str,
    target: str,
    kill_switch: Optional[KillSwitch] = None,
) -> None:
    """Refuse the call unless the decision permits it.

    Args:
        decision: What the caller was handed.
        category: The action category this method performs.
        target: The resolved path the method was asked to act on.
        kill_switch: The switch to re-check, or the process-wide
            one when None.

    Raises:
        DecisionRefused: No decision, a decision that does not
            allow execution, or a stale one from before a stop.
        DecisionReplayRefused: The decision names another
            operation or another target.
        DryRunRefused: The decision is a dry run.
        KillSwitchRefused: The switch was engaged after the
            decision was issued.
    """
    if not isinstance(decision, PermissionDecision):
        raise DecisionRefused(
            "refusing to run without a PermissionDecision from "
            "the permission gate"
        )
    if decision.dry_run:
        raise DryRunRefused(
            "dry-run decisions describe the action; they never execute"
        )
    # The switch is consulted before the decision's own verdict, so a
    # stop is reported as a stop. An action that needed confirming
    # anyway must not look merely unconfirmed while every operation
    # in the process is actually refused.
    switch = kill_switch if kill_switch is not None else get_kill_switch()
    if switch.is_killed():
        raise KillSwitchRefused(
            "kill switch engaged since this decision was issued"
        )
    if decision.blocked or not decision.allowed:
        raise DecisionRefused(
            f"the permission decision is {decision.decision}: "
            f"{decision.reason}"
        )
    if str(decision.action.category) != str(category):
        raise DecisionReplayRefused(
            f"decision was for {decision.action.category}, "
            f"not {category}"
        )
    if not _same_target(decision.action.target, target):
        raise DecisionReplayRefused(
            f"decision was for {decision.action.target}, not {target}"
        )


def _same_target(granted: str, acted_on: str) -> bool:
    """Return True when a decision covers the path about to be acted on.

    Both sides are resolved to absolute paths first. The gate decides
    on whatever spelling the caller used, while the Body acts on the
    resolved one, so ``docs/notes.md`` and its absolute form name the
    same file and a decision must not be refused merely because the
    two spellings differ. Resolving both sides keeps the comparison
    strict rather than looser: ``.env`` and ``C:\\...\\.env`` are the
    same credential file, and a decision for either must match both.

    A decision for a directory also covers everything beneath it, so
    a single approval can carry out a bulk operation the user was
    shown in full.

    Args:
        granted: The path the decision was issued for.
        acted_on: The resolved path the operation will touch.

    Returns:
        True when the decision covers this path.
    """
    if not str(granted).strip():
        return True
    if normalise(granted) == normalise(acted_on):
        return True
    approved = normalise(resolve_absolute(granted)[0])
    resolved = normalise(acted_on)
    if approved == resolved:
        return True
    return resolved.startswith(approved + os.sep)


def resolve_target(path: str, logger: Optional[Any] = None) -> str:
    """Resolve a path for an operation: absolute, then one symlink.

    Args:
        path: The path as it was given.
        logger: Activity logger, used to record the resolution.

    Returns:
        The normalised absolute path, with one level of symlink
        followed. The original spelling is what the permission
        decision matched; this is what the classification
        evaluates.
    """
    resolved, relative = resolve_absolute(path)
    if relative and escapes_project(path):
        raise PathTraversalRefused(
            f"relative path {path!r} resolves outside the project: {resolved}"
        )
    if is_symlink(resolved):
        real = resolve_symlink(resolved)
        if logger is not None:
            logger.log(
                "body",
                "symlink_resolved",
                link=resolved,
                target=real,
            )
        return real
    return resolved


def guard_read(path: str) -> None:
    """Refuse reads of secrets and protected locations.

    Args:
        path: The resolved path about to be read.

    Raises:
        SecretFileRefused: The path is a credential file.
        ProtectedPathRefused: The path is an operating system
            location.
    """
    if is_secret_file(path):
        raise SecretFileRefused(
            f"{path} is a secret file, blocked at every trust level"
        )
    if classify(path) == PROTECTED:
        raise ProtectedPathRefused(
            f"{path} is a protected system location"
        )


def guard_write(path: str) -> None:
    """Refuse writes to secrets, protected locations and ZUZZ itself.

    Args:
        path: The resolved path about to be written.

    Raises:
        SecretFileRefused: The path is a credential file.
        ProtectedPathRefused: The path is an operating system
            location.
        ZuzzCoreRefused: The path is ZUZZ's own code, logs or
            safety state.
    """
    guard_read(path)
    if is_zuzz_own_file(path):
        raise ZuzzCoreRefused(
            f"{path} is ZUZZ's own code or safety state (read-only)"
        )


def guard_deletion(path: str) -> None:
    """Refuse deletion of anything the write guard protects.

    Deleting is strictly worse than writing, so the same rules
    apply, and ZUZZ's own storage is included: the activity log
    and the kill switch flags are how a stop is remembered.

    Args:
        path: The resolved path about to be trashed.
    """
    guard_write(path)


def guard_listing(path: str) -> None:
    """Refuse to list protected locations or secret directories.

    Args:
        path: The resolved directory about to be listed.

    Raises:
        ProtectedPathRefused: The directory is an operating
            system location.
        SecretFileRefused: The directory is a credential
            directory such as ``.ssh``.
    """
    if is_secret_file(path):
        raise SecretFileRefused(
            f"{path} is a secret location, never listable"
        )
    if classify(path) == PROTECTED:
        raise ProtectedPathRefused(
            f"{path} is a protected system location"
        )


def category_for(operation: str) -> str:
    """Return the action category an operation maps onto.

    Args:
        operation: One of the Body's operation names.

    Returns:
        The config key: ``read_files``, ``write_files`` or
        ``delete_files``.

    Raises:
        ValueError: For an operation the Body does not know.
    """
    reads = ("read", "stat", "list", "search", "tree")
    writes = ("write", "copy", "move", "rename", "mkdir", "restore")
    deletes = ("delete", "empty_trash", "trash")
    if operation in reads:
        return str(ActionCategory.READ_FILES)
    if operation in writes:
        return str(ActionCategory.WRITE_FILES)
    if operation in deletes:
        return str(ActionCategory.DELETE_FILES)
    raise ValueError(f"unknown operation: {operation}")
