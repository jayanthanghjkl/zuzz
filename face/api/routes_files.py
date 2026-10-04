"""Face API — file operation routes.

Every file operation the UI can ask for, all of them
routed through the permission gate before anything
executes. The flow for every endpoint is the same:

1. Build the :class:`~brain.prefrontal_cortex.action.Action`.
2. Have the Amygdala score it.
3. Have the gate decide at the current trust level.
4. ``blocked`` → 403 (409 when the kill switch is
   engaged). ``ask`` → 202 with a ``confirmation_id``
   and everything the popup needs to show. ``auto`` →
   execute and return 200.

A confirmation is answered by re-posting the same
operation with ``confirmed: true`` and the
``confirmation_id``. The parked action is re-decided
at that moment — a stop that landed while the popup
was open still blocks — and only then upgraded to
allowed. A blocked decision can never be confirmed.

Status codes: 200 success, 202 awaiting confirmation,
400 bad request, 403 blocked by rule or classification,
404 nothing at that path, 409 kill switch or conflict,
413 above the size limit, 423 locked elsewhere, 500
internal (details logged, generic message returned).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.permission_gate import AUTO, PermissionGate
from body.fingers import guards
from body.fingers.exceptions import BodyRefused
from body.fingers.file_backup import (
    list_backups,
    restore_backup,
)
from body.fingers.file_classifier import resolve_absolute
from core.audit import (
    BLOCKED,
    CONFIRMATION_GRANTED,
    CONFIRMATION_REQUESTED,
    PRECHECK_REFUSAL,
)
from core.paths import classify, normalise

from face.api.file_confirmations import (
    build_preview,
    confirmation_payload,
)

router = APIRouter()

#: Longest target written into an audit record. Long enough for any real path,
#: short enough that a caller cannot stuff the log with megabytes.
_MAX_TARGET_CHARS = 240


class FileRequest(BaseModel):
    """Fields every file operation accepts."""

    dry_run: bool = Field(
        False, description="Preview the action without running it"
    )
    confirmed: bool = Field(
        False, description="The user approved the 202 prompt"
    )
    confirmation_id: str = Field(
        "", description="Id returned with the 202"
    )


class ReadRequest(FileRequest):
    """Read a file."""

    path: str
    max_size: int = 5_242_880
    binary: bool = False


class WriteRequest(FileRequest):
    """Write a file."""

    path: str
    content: str = ""
    mode: str = Field("w", description="'w' or 'a'")


class TwoPathRequest(FileRequest):
    """An operation with a source and a destination."""

    src: str
    dst: str


class RenameRequest(FileRequest):
    """Rename a file."""

    path: str
    new_name: str


class PathRequest(FileRequest):
    """An operation on one path."""

    path: str


class MkdirRequest(FileRequest):
    """Make a directory."""

    path: str
    parents: bool = False


class ListRequest(FileRequest):
    """List a directory."""

    path: str
    limit: int = 100
    offset: int = 0
    include_hidden: bool = False


class SearchRequest(FileRequest):
    """Search a tree."""

    pattern: str
    root: str
    max_depth: int = 10
    include_hidden: bool = False
    regex: bool = False


class TreeRequest(FileRequest):
    """Render a directory tree."""

    path: str
    max_depth: int = 3


class RestoreRequest(FileRequest):
    """Restore from the trash."""

    trash_id: str


class EmptyTrashRequest(FileRequest):
    """Purge the trash."""

    older_than_days: float = 0


class RestoreBackupRequest(FileRequest):
    """Restore a backup version."""

    path: str
    backup_version: int


def _gate(
    request: Request, category: str, target: str, dry_run: bool = False
):
    """Score and decide one action.

    Args:
        request: Used to reach the trust level.
        category: The action category.
        target: What the action acts on, already resolved
            to an absolute path.
        dry_run: Whether the action is a preview.

    Returns:
        The action, its threat assessment and the decision.
    """
    level = request.app.state.trust.current()
    action = Action(
        category=ActionCategory(category), target=target, dry_run=dry_run
    )
    threat = ThreatDetector().assess(action)
    decision = PermissionGate().decide(action, level, threat)
    return action, threat, decision


def _filesystem_target(path: str) -> str:
    """Resolve a request path the way the Body will.

    Args:
        path: A path as the UI sent it.

    Returns:
        The absolute, normalised path, so the decision the
        gate makes names the same path the Body acts on.
    """
    return resolve_absolute(path)[0]


def _precheck(operation: str, raw_target: str, raw_extra: str = "") -> None:
    """Apply the Body's classification before a prompt is shown.

    A confirmation the Body would refuse anyway is not a
    confirmation worth showing, so secrets, protected
    locations and ZUZZ's own files are refused here, with
    the same guards the Body runs, before the gate is even
    consulted.

    The *raw* path is used, not the one already resolved for the
    gate, and that ordering matters: traversal is only detectable
    while the path is still relative. Resolving first and handing
    the guard an absolute path makes ``../../../etc/passwd``
    indistinguishable from a legitimate absolute path, which is
    exactly the spelling an attack would use.

    Args:
        operation: The operation name.
        raw_target: The operation's target path as the UI sent it.
        raw_extra: A second path (copy/move destinations), guarded
            as a write.

    Raises:
        PathTraversalRefused: The relative path escapes the project.
        ProtectedPathRefused: The target is somewhere the
            Body will never go.
    """
    real = guards.resolve_target(raw_target, None)
    if raw_extra:
        guards.guard_write(guards.resolve_target(raw_extra, None))
    if operation in ("read", "stat", "list", "search", "tree"):
        guards.guard_read(real)
    elif operation in (
        "write", "copy", "move", "rename", "mkdir", "restore-backup",
    ):
        guards.guard_write(real)
    elif operation in ("delete", "trash"):
        guards.guard_deletion(real)


def _audit(request: Request, event: str, category: str, target: str,
           decision: PermissionDecision) -> None:
    """Record a safety event in the audit log."""
    request.app.state.audit.record(
        event,
        decision.reason,
        category=category,
        target=target,
        level=decision.decision,
        trust_level=decision.trust_level,
        rule_id=decision.rule_id or "",
    )


def _safe_target(path: str) -> str:
    """Return a path safe to write into an append-only log.

    A precheck refusal exists precisely to record the path somebody asked for, so
    the path is not redacted away — a security review that cannot see *which* file
    was attempted is not a review. What is redacted is the account name inside it:
    ``C:\\Users\\jayan\\.ssh\\id_rsa`` becomes ``C:\\Users\\<user>\\.ssh\\id_rsa``.
    The log survives on disk and gets copied into bug reports, and a machine's
    account name is not something an audit entry needs to carry in order to be
    useful.

    Args:
        path: The path as the UI spelled it.

    Returns:
        The path with the profile directory collapsed, bounded in length.
    """
    # A callable replacement, not a string: ``re.sub`` treats a string replacement
    # as an escape template, and the ``\U`` in ``\Users`` is not a valid escape, so
    # the obvious spelling raises bad-escape rather than redacting anything.
    text = re.sub(
        r"(?i)\\users\\[^\\]+", lambda _match: "\\Users\\<user>", str(path or "")
    )
    return text[:_MAX_TARGET_CHARS]


def _audit_precheck(
    request: Request,
    operation: str,
    exc: Exception,
    raw_target: str,
    raw_extra: str = "",
) -> None:
    """Record a refusal that happened before the gate was consulted.

    Session 9, Defect 2. :func:`_precheck` runs the Body's classification guards so
    that a confirmation the Body would refuse anyway is never shown. Until this
    function existed the refusal was correct and completely invisible: an attack
    asking for ``storage/vault.json`` or ``../../.env`` received a 403 and left
    nothing behind, so the audit log — the one artefact a security review reads —
    could not distinguish "nothing was attempted" from "something was attempted and
    refused".

    The record carries the same fields a gate block carries (``category``, ``target``,
    ``level``, ``trust_level``, ``rule_id``) so one reader and one query cover both,
    plus the things only a precheck refusal knows: which guard refused, how the path
    classified, and where the request came from.

    Args:
        request: Used to reach the audit log, the trust level and the client.
        operation: The operation name, e.g. ``read`` or ``delete``.
        exc: The refusal raised by the guard.
        raw_target: The target as the UI spelled it, before resolution.
        raw_extra: A second path, for copy and move.
    """
    from body.fingers.exceptions import (
        PathTraversalRefused,
        SecretFileRefused,
        ZuzzCoreRefused,
    )

    from body.fingers.file_classifier import is_secret_file

    refusal = type(exc).__name__
    classification = "unknown"
    secret = False
    try:
        classification = classify(_safe_target(raw_target)) or "unknown"
        secret = is_secret_file(raw_target)
    except Exception:  # pragma: no cover - classification is best-effort
        classification = "unknown"

    if isinstance(exc, SecretFileRefused):
        classification = "secret"
        secret = True
    elif isinstance(exc, ZuzzCoreRefused):
        classification = "zuzz_core"
    elif isinstance(exc, PathTraversalRefused):
        classification = "traversal"

    record: Dict[str, Any] = {
        "category": PRECHECK_REFUSAL,
        "target": _safe_target(raw_target),
        "level": "refused",
        "trust_level": request.app.state.trust.current(),
        "rule_id": "precheck",
        "refusal_stage": PRECHECK_REFUSAL,
        "refusal_type": refusal,
        "operation": operation,
        "classification": classification,
        "secret_file": secret,
    }
    if raw_extra:
        record["secondary_target"] = _safe_target(raw_extra)
    origin = _request_origin(request)
    record.update(origin)
    request.app.state.audit.record(PRECHECK_REFUSAL, str(exc), **record)


def _request_origin(request: Request) -> Dict[str, Any]:
    """Describe where a request came from, for the audit record.

    Args:
        request: The incoming request.

    Returns:
        The method and path, and the client host when the transport supplied one.
        A host is a best-effort attribution — it is absent for in-process calls and
        trivially spoofable, which is why it is supporting detail rather than the
        identifying field.
    """
    origin: Dict[str, Any] = {
        "request_method": request.method,
        "request_path": request.url.path,
    }
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        origin["request_origin"] = client.host
    else:
        origin["request_origin"] = "in-process"
    return origin


async def _run(
    request: Request,
    body: FileRequest,
    operation: str,
    category: str,
    target: str,
    execute: Callable[[PermissionDecision], Any],
    precheck: bool = True,
    extra_target: str = "",
    raw_target: str = "",
    raw_extra: str = "",
) -> Dict[str, Any]:
    """Gate, confirm and run one file operation.

    Args:
        request: Used to reach the app's collaborators.
        body: The request, including confirmation evidence.
        operation: The operation name, for previews.
        category: The action category for the gate.
        target: The action's target, already resolved to an
            absolute path.
        execute: The Body call, invoked only with an
            allowed decision.
        precheck: Apply the Body's classification guards
            before prompting; disabled for targets that are
            not filesystem paths.
        extra_target: A second path guarded as a write.
        raw_target: The target as the UI spelled it, so the
            pre-check can still see whether it was relative.
        raw_extra: The second path as the UI spelled it.

    Returns:
        The API response: a plan, a confirmation prompt,
        or the operation's result.

    Raises:
        HTTPException: Mapped from the Body's refusals.
    """
    # A stop outranks every other answer, including the
    # classification below: while engaged nothing is
    # prompted, planned or executed.
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409,
            detail="kill switch engaged; reset the stop to continue",
        )
    if precheck:
        try:
            _precheck(
                operation,
                raw_target or target,
                raw_extra or extra_target,
            )
        except BodyRefused as exc:
            _audit_precheck(request, operation, exc, raw_target or target, raw_extra)
            raise _refuse(exc) from exc
    action, threat, decision = _gate(request, category, target, body.dry_run)
    if body.dry_run:
        return {
            "status": "dry_run",
            "plan": build_preview(operation, target),
            "decision": decision.to_dict(),
            "threat": threat.to_dict(),
        }
    if decision.blocked:
        _audit(request, BLOCKED, category, target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if decision.requires_confirmation and not body.confirmed:
        preview = build_preview(
            operation,
            target,
            trash_stats=(
                request.app.state.trash_manager.trash_stats()
                if operation == "empty-trash"
                else None
            ),
        )
        confirmation_id = request.app.state.confirmations.create(
            action, decision, preview
        )
        _audit(
            request, CONFIRMATION_REQUESTED, category, target, decision
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "confirmation_required",
                **confirmation_payload(
                    confirmation_id, operation, decision, threat, preview
                ),
            },
        )
    if decision.requires_confirmation:
        decision = await _confirm(request, body, category, target)
    try:
        result = execute(decision)
    except BodyRefused as exc:
        _audit(
            request, BLOCKED, category, target,
            replace(decision, reason=str(exc)),
        )
        raise _refuse(exc) from exc
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        request.app.state.logger.log(
            "face", "file_api_error", operation=operation,
            target=target, error=str(exc),
        )
        raise HTTPException(status_code=500, detail="internal error") from exc
    return {"status": "ok", "result": result,
            "decision": decision.to_dict()}


async def _confirm(
    request: Request, body: FileRequest, category: str, target: str
) -> PermissionDecision:
    """Answer a parked confirmation.

    Args:
        request: Used to reach the confirmation registry.
        body: The confirmation evidence.
        category: The action category.
        target: The action's target.

    Returns:
        The decision, upgraded to allowed by the user's
        confirmation.

    Raises:
        HTTPException: 400 for an unknown id or a mismatch,
            403 when the re-decision is now blocked.
    """
    entry = request.app.state.confirmations.pop(body.confirmation_id)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail="unknown or expired confirmation_id",
        )
    parked = entry["action"]
    if str(parked.category) != category or normalise(
        parked.target
    ) != normalise(target):
        raise HTTPException(
            status_code=400,
            detail="confirmation does not match this operation",
        )
    fresh_action, _threat, fresh = _gate(request, category, target)
    if fresh.blocked:
        _audit(request, BLOCKED, category, target, fresh)
        raise HTTPException(status_code=403, detail=fresh.reason)
    _audit(request, CONFIRMATION_GRANTED, category, target, fresh)
    # The user has now confirmed this specific action, so the
    # "ask" becomes an allow. Only the gate's "ask" is ever
    # upgraded here — a blocked decision never reaches this
    # point, and cannot.
    return replace(
        fresh, allowed=True, requires_confirmation=False, decision=AUTO
    )


def _refuse(exc: Exception) -> HTTPException:
    """Map a Body refusal onto an HTTP status.

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise.
    """
    if isinstance(exc, BodyRefused):
        from body.fingers.exceptions import (
            DecisionRefused,
            DryRunRefused,
            FileLockedError,
            FileTooLarge,
            KillSwitchRefused,
            ProtectedPathRefused,
            RestoreConflict,
            TrashError,
        )

        if isinstance(exc, KillSwitchRefused):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, (ProtectedPathRefused, DecisionRefused,
                            DryRunRefused)):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, FileTooLarge):
            return HTTPException(status_code=413, detail=str(exc))
        if isinstance(exc, FileLockedError):
            return HTTPException(status_code=423, detail=str(exc))
        if isinstance(exc, (RestoreConflict, TrashError)):
            return HTTPException(status_code=409, detail=str(exc))
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


@router.post("/api/files/read")
async def read_file(request: Request, body: ReadRequest) -> Dict[str, Any]:
    """Read a file, through the gate."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.read(
            decision, body.path, body.max_size, body.binary
        )

    return await _run(
        request,
        body,
        "read",
        "read_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/write")
async def write_file(request: Request, body: WriteRequest) -> Dict[str, Any]:
    """Write a file — a backup is made first when overwriting."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.write(decision, body.path, body.content, body.mode)

    return await _run(
        request,
        body,
        "write",
        "write_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/copy")
async def copy_file(request: Request, body: TwoPathRequest) -> Dict[str, Any]:
    """Copy a file."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.copy(decision, body.src, body.dst)

    return await _run(
        request,
        body,
        "copy",
        "write_files",
        _filesystem_target(body.src),
        execute,
        extra_target=_filesystem_target(body.dst),
        raw_target=body.src,
        raw_extra=body.dst,
    )


@router.post("/api/files/move")
async def move_file(request: Request, body: TwoPathRequest) -> Dict[str, Any]:
    """Move a file."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.move(decision, body.src, body.dst)

    return await _run(
        request,
        body,
        "move",
        "write_files",
        _filesystem_target(body.src),
        execute,
        extra_target=_filesystem_target(body.dst),
        raw_target=body.src,
        raw_extra=body.dst,
    )


@router.post("/api/files/rename")
async def rename_file(request: Request, body: RenameRequest) -> Dict[str, Any]:
    """Rename a file."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.rename(decision, body.path, body.new_name)

    return await _run(
        request,
        body,
        "rename",
        "write_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/delete")
async def delete_file(request: Request, body: PathRequest) -> Dict[str, Any]:
    """Delete a file — always into the trash, never outright."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.delete(decision, body.path)

    return await _run(
        request,
        body,
        "delete",
        "delete_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/mkdir")
async def make_directory(request: Request, body: MkdirRequest) -> Dict[str, Any]:
    """Create a directory."""
    managers = request.app.state.directory_manager

    def execute(decision: PermissionDecision):
        return managers.mkdir(decision, body.path, body.parents)

    return await _run(
        request,
        body,
        "mkdir",
        "write_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/list")
async def list_directory(request: Request, body: ListRequest) -> Dict[str, Any]:
    """List a directory, paginated."""
    managers = request.app.state.directory_manager

    def execute(decision: PermissionDecision):
        return managers.list(
            decision, body.path, body.limit, body.offset, body.include_hidden
        )

    return await _run(
        request,
        body,
        "list",
        "read_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/search")
async def search_files(request: Request, body: SearchRequest) -> Dict[str, Any]:
    """Search a tree by glob or regex pattern."""
    managers = request.app.state.directory_manager

    def execute(decision: PermissionDecision):
        return managers.search(
            decision,
            body.pattern,
            body.root,
            body.max_depth,
            body.include_hidden,
            body.regex,
        )

    return await _run(
        request,
        body,
        "search",
        "read_files",
        _filesystem_target(body.root),
        execute,
    )


@router.post("/api/files/tree")
async def directory_tree(request: Request, body: TreeRequest) -> Dict[str, Any]:
    """Render a directory tree."""
    managers = request.app.state.directory_manager

    def execute(decision: PermissionDecision):
        return managers.tree(decision, body.path, body.max_depth)

    return await _run(
        request,
        body,
        "tree",
        "read_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.post("/api/files/stat")
async def stat_file(request: Request, body: PathRequest) -> Dict[str, Any]:
    """Return a file's metadata."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return managers.stat(decision, body.path)

    return await _run(
        request,
        body,
        "stat",
        "read_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )


@router.get("/api/files/trash")
async def list_trash(request: Request) -> Dict[str, Any]:
    """List trashed items and the trash's size."""
    trash_manager = request.app.state.trash_manager
    return {
        "status": "ok",
        "items": trash_manager.list_trash(),
        "stats": trash_manager.trash_stats(),
    }


@router.post("/api/files/restore")
async def restore_trashed(request: Request, body: RestoreRequest) -> Dict[str, Any]:
    """Restore a trashed item to its original path."""
    trash_manager = request.app.state.trash_manager
    target = f"trash:{body.trash_id}"

    def execute(decision: PermissionDecision):
        return trash_manager.restore(decision, body.trash_id)

    return await _run(
        request, body, "restore", "write_files", target, execute,
        precheck=False,
    )


@router.post("/api/files/empty-trash")
async def empty_trash(request: Request, body: EmptyTrashRequest) -> Dict[str, Any]:
    """Purge the trash — each item goes to the OS recycle bin."""
    trash_manager = request.app.state.trash_manager

    def execute(decision: PermissionDecision):
        return trash_manager.empty_trash(decision, body.older_than_days)

    return await _run(
        request, body, "empty-trash", "delete_files", "trash", execute,
        precheck=False,
    )


@router.get("/api/files/backups")
async def list_file_backups(request: Request, path: str) -> Dict[str, Any]:
    """List the backups kept beside a file."""
    return {
        "status": "ok",
        "path": normalise(path),
        "backups": list_backups(path),
    }


@router.post("/api/files/restore-backup")
async def restore_file_backup(
    request: Request, body: RestoreBackupRequest
) -> Dict[str, Any]:
    """Restore one backup version over the current file."""
    managers = request.app.state.file_manager

    def execute(decision: PermissionDecision):
        return restore_backup(decision, body.path, body.backup_version)

    return await _run(
        request,
        body,
        "restore-backup",
        "write_files",
        _filesystem_target(body.path),
        execute,
        raw_target=body.path,
    )
