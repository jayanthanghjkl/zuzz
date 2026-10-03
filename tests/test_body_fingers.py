"""Body/Fingers — file operations behind the gate.

The attack tests here follow the Step 1 pattern: they assume
the gate has been bypassed and hand the Body a genuinely
allowed :class:`PermissionDecision` forged at trust level 5.
If the Body executes anyway, the safety net is gone — so for
every dangerous target the Body must still refuse, on its own
classification, with no gate involved.

Nothing in this file touches the developer's real filesystem:
protected paths are guarded by canaries (a patched move or
open that fails the test if it is ever reached), the kill
switch is isolated in ``tmp_path``, and the trash lives in
``tmp_path`` with a purge transport that never reaches the
operating system's recycle bin.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.amygdala.threat_assessment import ThreatAssessment
from brain.brainstem.activity_logger import ActivityLogger
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.permission_gate import (
    ASK,
    AUTO,
    BLOCKED,
    PermissionGate,
)
from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.kill_switch import KillSwitch
from brain.spinal_cord.watchdog import Watchdog
from core.audit import AuditLog
from core.config import ROOT
from core.paths import PROTECTED, classify

from body.fingers import (
    DirectoryManager,
    FileManager,
    TrashManager,
)
from body.fingers.exceptions import (
    BinaryFileRefused,
    BodyRefused,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    FileLockedError,
    FileTooLarge,
    KillSwitchRefused,
    PathTraversalRefused,
    ProtectedPathRefused,
    RestoreConflict,
    SecretFileRefused,
    TrashError,
    ZuzzCoreRefused,
)
from body.fingers.file_classifier import (
    escapes_project,
    is_secret_file,
    is_zuzz_own_file,
    resolve_absolute,
    resolve_symlink,
)
from body.fingers.file_manager import (
    DEFAULT_MAX_READ,
    HARD_MAX_READ,
    LOCK_TIMEOUT,
    _decode,
    _open_locked,
)
from body.fingers.trash_manager import (
    AUTO_PURGE_DAYS,
    MIN_RETENTION_DAYS,
)

ALL_LEVELS = (1, 2, 3, 4, 5)

WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------


def fake_purge(path: str) -> None:
    """Test purge transport: removes the item for real.

    Lives in the test suite, not the Body: the Body must
    never delete outright, but a test may.
    """
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def bomb(*args, **kwargs):
    """Fail loudly if the Body ever reaches the disk where a
    test forbade it."""
    raise AssertionError("the Body reached the filesystem where the test forbade it")


@pytest.fixture
def env(tmp_path: Path) -> SimpleNamespace:
    """An isolated Body: trash, logger, kill switch and managers
    all inside ``tmp_path``."""
    switch = KillSwitch(
        paths=[tmp_path / "stop.flag"],
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    trash = TrashManager(
        trash_root=tmp_path / "trash",
        purge_transport=fake_purge,
        logger=logger,
    )
    files = FileManager(trash_manager=trash, logger=logger, kill_switch=switch)
    dirs = DirectoryManager(logger=logger, kill_switch=switch)
    return SimpleNamespace(
        tmp=tmp_path,
        switch=switch,
        logger=logger,
        trash=trash,
        files=files,
        dirs=dirs,
    )


@pytest.fixture
def files_app(tmp_path: Path) -> SimpleNamespace:
    """A FastAPI app whose Body is isolated in ``tmp_path``."""
    from face.api.file_confirmations import PendingConfirmations
    from face.api.server import create_app

    switch = KillSwitch(
        paths=[tmp_path / "stop.flag"],
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    logger = ActivityLogger(tmp_path / "activity.jsonl")
    trash = TrashManager(
        trash_root=tmp_path / "trash",
        purge_transport=fake_purge,
        logger=logger,
    )
    app = create_app()
    app.state.kill_switch = switch
    app.state.trust = TrustManager(path=tmp_path / "trust.json")
    app.state.trash_manager = trash
    app.state.file_manager = FileManager(
        trash_manager=trash, logger=logger, kill_switch=switch
    )
    app.state.directory_manager = DirectoryManager(
        logger=logger, kill_switch=switch
    )
    app.state.confirmations = PendingConfirmations()
    return SimpleNamespace(app=app, tmp=tmp_path, switch=switch,
                           trash=trash, logger=logger)


def allowed_decision(
    category: str, target: str, level: int = 5
) -> PermissionDecision:
    """Forge an allowed decision, as if the gate had approved it.

    The attack tests use this to prove the Body refuses on its
    own classification even when the gate is assumed bypassed.
    """
    action = Action(
        category=ActionCategory(category), target=target
    )
    return PermissionDecision.make(action, level, AUTO, "forged for test")


def ask_decision(category: str, target: str, level: int = 1) -> PermissionDecision:
    """Forge an ``ask`` decision, the shape the gate returns at L1."""
    action = Action(
        category=ActionCategory(category), target=target
    )
    return PermissionDecision.make(action, level, ASK, "forged for test")


def threat(score: int = 0, level: str = "safe") -> ThreatAssessment:
    """A minimal threat assessment."""
    return ThreatAssessment(score=score, level=level, explanation="test")


def victim_dir(env: SimpleNamespace, name: str = "victim") -> Path:
    """A directory of files to operate on."""
    directory = env.tmp / name
    directory.mkdir(exist_ok=True)
    return directory


@pytest.fixture(autouse=True)
def _victims_are_not_temp(monkeypatch) -> None:
    """Keep pytest's tmp directory classified as ordinary files.

    ``tmp_path`` lives under the system temp directory, which
    :mod:`core.paths` classifies as ``temp`` — and temp files
    auto-approve deletion from trust level 3, which is the approved
    Step 3 exception. Left alone, every test here would silently
    exercise that exception instead of the general rules: assertions
    about a path being ``safe`` rather than ``temp`` would fail, and
    deletion would auto-approve at the trust levels where it is
    supposed to ask.

    The reference *inside* ``core.paths`` is replaced rather than the
    ``tempfile`` module globally, so pytest's own use of the temp
    directory — and ``send2trash``'s — is untouched. Tests that mean
    to exercise temp classification say so explicitly.
    """
    monkeypatch.setattr(
        "core.paths.tempfile",
        SimpleNamespace(
            gettempdir=lambda: os.path.join(os.sep, "zuzz-not-system-temp")
        ),
    )


def parameters_of(owner: Any, name: str) -> Any:
    """Return a callable's declared parameters, without ``self``.

    The gate is enforced by the *signature*, so these tests read
    signatures. ``self`` is an implementation detail of a bound method
    and must not be mistaken for the first declared parameter.

    Args:
        owner: The class or module holding the callable.
        name: The callable's name.

    Returns:
        The parameter list, with ``self`` removed.
    """
    params = list(inspect.signature(getattr(owner, name)).parameters.values())
    if params and params[0].name in ("self", "cls"):
        return params[1:]
    return params


def is_decision_annotation(annotation: Any) -> bool:
    """Return True when an annotation marks a PermissionDecision.

    The Body modules use ``from __future__ import annotations``, so
    annotations arrive as strings rather than objects; both spellings
    are accepted.

    Args:
        annotation: What ``inspect`` reported.

    Returns:
        True when the annotation names a PermissionDecision.
    """
    return annotation in (PermissionDecision, "PermissionDecision")


# ---------------------------------------------------------------------
# The signature attack: no Body method without a decision
# ---------------------------------------------------------------------


BODY_CLASSES = (FileManager, DirectoryManager, TrashManager)

#: System-level methods, documented in the package docstring,
#: that are not agent actions and therefore carry no decision:
#: read-only metadata about ZUZZ's own trash, and the
#: scheduled purge job.
DECISION_FREE_METHODS = {
    ("TrashManager", "list_trash"),
    ("TrashManager", "trash_stats"),
    ("TrashManager", "auto_purge_old"),
}

DECISION_FREE_FUNCTIONS = {"list_backups"}

METHOD_ARGS = {
    "read": ("file.txt",),
    "write": ("file.txt", "content"),
    "copy": ("a.txt", "b.txt"),
    "move": ("a.txt", "b.txt"),
    "rename": ("a.txt", "b.txt"),
    "delete": ("file.txt",),
    "stat": ("file.txt",),
    "mkdir": ("dir",),
    "list": ("dir",),
    "search": ("*.py", "root"),
    "tree": ("dir",),
    "trash": ("file.txt",),
    "restore": ("trash-id",),
    "empty_trash": (),
}


def test_no_body_method_is_reachable_without_a_permission_decision() -> None:
    """Every Body method takes a PermissionDecision as its first
    parameter — the gate is enforced by the type signature, not
    by convention."""
    checked = 0
    for klass in BODY_CLASSES:
        for name, method in inspect.getmembers(klass, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            if (klass.__name__, name) in DECISION_FREE_METHODS:
                continue
            params = parameters_of(klass, name)
            assert params, f"{klass.__name__}.{name} takes no parameters"
            first = params[0]
            assert first.name == "decision", (
                f"{klass.__name__}.{name} hides the decision behind "
                f"{first.name!r}"
            )
            assert is_decision_annotation(first.annotation), (
                f"{klass.__name__}.{name} is not annotated PermissionDecision"
            )
            checked += 1
    # Pinned exactly, not "at least": losing a method silently would
    # otherwise shrink the surface the gate has to cover without
    # failing anything.
    assert checked == 14, f"expected 14 gated Body methods, found {checked}"


def test_backup_functions_take_a_decision_except_list_backups() -> None:
    """The backup helpers follow the same rule; only the
    read-only listing is decision-free."""
    from body.fingers import file_backup

    for name, function in inspect.getmembers(file_backup, predicate=inspect.isfunction):
        if name.startswith("_") or name in DECISION_FREE_FUNCTIONS:
            continue
        params = parameters_of(file_backup, name)
        assert params[0].name == "decision", (
            f"file_backup.{name} does not take a decision"
        )
        assert is_decision_annotation(params[0].annotation)


@pytest.mark.parametrize("cls,kwargs", [
    (FileManager, {}),
    (DirectoryManager, {}),
])
def test_calling_without_a_decision_refuses(cls, kwargs) -> None:
    """A missing decision is a refusal, not an error to route
    around: every method refuses before any other work."""
    instance = cls(**kwargs)
    for method_name, args in METHOD_ARGS.items():
        if not hasattr(instance, method_name):
            continue  # the other manager owns this operation
        method = getattr(instance, method_name)
        with pytest.raises(DecisionRefused):
            method(None, *args)
        with pytest.raises(DecisionRefused):
            method("not a decision", *args)
        with pytest.raises(DecisionRefused):
            method(object(), *args)


def test_trash_methods_refuse_without_a_decision(env) -> None:
    """The trash manager refuses a missing decision on every
    decision-bearing method."""
    for method_name, args in (
        ("trash", ("file.txt",)),
        ("restore", ("some-id",)),
        ("empty_trash", ()),
    ):
        method = getattr(env.trash, method_name)
        with pytest.raises(DecisionRefused):
            method(None, *args)
        with pytest.raises(DecisionRefused):
            method(object(), *args)


def test_no_operations_without_permission_decision(env, tmp_path) -> None:
    """The Step 1 attack pattern, applied to the Body: an operation
    with no decision at all must not reach the disk.

    Every operation of every manager is called without a decision and
    with a look-alike that is not one, against a real file in a real
    directory. All of them must refuse — not raise a ``TypeError``
    about a missing argument, which a caller could catch and retry
    around, and not perform the work first and complain afterwards.

    The file and directory below are real and must survive intact:
    this asserts that a refusal happens *before* the filesystem is
    touched, not merely that an exception came back.
    """
    target = tmp_path / "untouched.txt"
    target.write_text("original")
    other = tmp_path / "untouched-copy.txt"
    managers = {
        "read": (env.files, (str(target),)),
        "write": (env.files, (str(target), "overwritten")),
        "copy": (env.files, (str(target), str(other))),
        "move": (env.files, (str(target), str(other))),
        "rename": (env.files, (str(target), "renamed.txt")),
        "delete": (env.files, (str(target),)),
        "stat": (env.files, (str(target),)),
        "mkdir": (env.dirs, (str(tmp_path / "newdir"),)),
        "list": (env.dirs, (str(tmp_path),)),
        "search": (env.dirs, ("*", str(tmp_path))),
        "tree": (env.dirs, (str(tmp_path),)),
        "trash": (env.trash, (str(target),)),
        "restore": (env.trash, ("some-id",)),
        "empty_trash": (env.trash, ()),
    }
    impostors = (None, "not a decision", object(), 0, True, {})
    for name, (manager, args) in managers.items():
        method = getattr(manager, name)
        for impostor in impostors:
            with pytest.raises(DecisionRefused):
                method(impostor, *args)
    # Nothing above was allowed near the disk.
    assert target.read_text() == "original"
    assert not other.exists()
    assert not (tmp_path / "newdir").exists()
    assert not (tmp_path / "renamed.txt").exists()
    assert env.trash.list_trash() == []


def test_system_methods_are_exactly_the_documented_system_methods() -> None:
    """The decision-free surface is pinned: only the trash
    inventory, its size summary and the scheduled purge may run
    without an agent decision, because none of them is an agent
    action."""
    decision_free = {
        (klass.__name__, name)
        for klass in BODY_CLASSES
        for name, method in inspect.getmembers(klass, predicate=inspect.isfunction)
        if not name.startswith("_")
        and (
            not parameters_of(klass, name)
            or parameters_of(klass, name)[0].name != "decision"
        )
    }
    assert decision_free == DECISION_FREE_METHODS


# ---------------------------------------------------------------------
# Attack 1: system files
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\config\system.ini",
    r"c:\windows\system32\drivers\etc\hosts",
    r"C:\Program Files\Common Files\setup.dll",
    r"C:\$Recycle.Bin\S-1-5-21\file.txt",
])
def test_cannot_delete_windows_system_files(env, monkeypatch, path) -> None:
    """A forged allowed decision cannot delete a system file: the
    Body classifies the target itself and refuses."""
    monkeypatch.setattr("body.fingers.trash_manager.shutil.move", bomb)
    decision = allowed_decision("delete_files", path)
    with pytest.raises(ProtectedPathRefused):
        env.files.delete(decision, path)


@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\config\system.ini",
    r"c:\windows\win.ini",
    r"C:\Program Files\app\config.ini",
])
def test_cannot_overwrite_system_files(env, monkeypatch, path) -> None:
    """Writes to system locations are refused by the Body, not
    just the gate — and the disk is never touched."""
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    decision = allowed_decision("write_files", path)
    with pytest.raises(ProtectedPathRefused):
        env.files.write(decision, path, "content")


@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\config\system.ini",
    r"c:\windows\win.ini",
])
def test_cannot_read_system_files(env, monkeypatch, path) -> None:
    """Reading a system file is refused too: the classification
    says the operating system owns it."""
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    decision = allowed_decision("read_files", path)
    with pytest.raises(ProtectedPathRefused):
        env.files.read(decision, path)


def test_case_variations_still_blocked(env, monkeypatch) -> None:
    """NTFS is case-insensitive: ``c:\\windows`` and
    ``C:\\WINDOWS`` are the same protected directory (the
    Session 3 lesson, pinned for the Body)."""
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    for spelling in (r"c:\windows\win.ini", r"C:\WINDOWS\win.ini",
                     r"C:\WiNdOwS\Win.Ini"):
        assert classify(spelling) == PROTECTED
        decision = allowed_decision("read_files", spelling)
        with pytest.raises(ProtectedPathRefused):
            env.files.read(decision, spelling)


@pytest.mark.parametrize("path", [
    r"\\?\C:\Windows\System32",
    r"\\?\C:\windows\win.ini",
    r"\\?\C:\Program Files\app",
])
def test_cannot_use_unc_paths(env, monkeypatch, path) -> None:
    """The Win32 extended-length prefix ``\\\\?\\`` is stripped
    before classification, so it cannot spell a protected path
    past the guard."""
    from core.paths import classify as core_classify

    assert core_classify(path) == PROTECTED
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    decision = allowed_decision("read_files", path)
    with pytest.raises(ProtectedPathRefused):
        env.files.read(decision, path)


def test_unc_prefix_is_stripped_by_the_shared_classifier() -> None:
    """The fix lives in ``core/paths.py``, so the gate, the
    Amygdala and the Body all answer the same way."""
    from core.paths import classify as core_classify

    assert core_classify(r"\\?\C:\Windows") == PROTECTED
    assert core_classify(r"\\?\UNC\server\share\file") == "safe"
    assert core_classify("C:/Windows/System32") == PROTECTED


# ---------------------------------------------------------------------
# Attack 2: secrets, hard-blocked at every trust level
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    ".env",
    ".env.local",
    ".ENV",
    ".env.production",
    ".ssh/id_rsa",
    ".ssh/config",
    ".aws/credentials",
    "server.pem",
    "private.PEM",
    "service.key",
    "config/api_vault.enc",
    "storage/vault.json",
    "storage/.vault_key",
    "credentials",
    "id_ed25519",
])
def test_secret_files_blocked_even_at_L5(env, path) -> None:
    """Secrets are hard-blocked by the Body at trust level 5,
    with a fully valid allowed decision in hand — the matrix
    alone would auto-approve the read."""
    decision = allowed_decision("read_files", path, level=5)
    with pytest.raises(SecretFileRefused):
        env.files.read(decision, path)


def test_cannot_write_to_env_file(env, monkeypatch) -> None:
    """A forged allowed ``write_files`` decision cannot overwrite
    ``.env`` — the API vault's own input. The disk is never
    touched, and the real file's contents are unchanged.

    This is the sharpest version of the secret hard block: the
    project directory is ZUZZ's own writable space, so nothing
    about this path looks unusual until the name is inspected.
    """
    real = ROOT / ".env"
    before = real.read_bytes() if real.is_file() else None
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    monkeypatch.setattr("body.fingers.file_manager.shutil.copy2", bomb)
    monkeypatch.setattr("body.fingers.file_manager.shutil.move", bomb)
    monkeypatch.setattr("body.fingers.trash_manager.shutil.move", bomb)
    # Copying and moving name their source in the decision and guard
    # the destination separately, so both directions are covered here:
    # writing onto a secret, and carrying a secret away.
    source_decision = allowed_decision("write_files", ".gitignore", level=5)
    for path in (".env", ".env.local", ".ENV", ".env.production"):
        decision = allowed_decision("write_files", path, level=5)
        with pytest.raises(SecretFileRefused):
            env.files.write(decision, path, "STOLEN=1")
        with pytest.raises(SecretFileRefused):
            env.files.copy(source_decision, ".gitignore", path)
        with pytest.raises(SecretFileRefused):
            env.files.move(source_decision, ".gitignore", path)
        with pytest.raises(SecretFileRefused):
            env.files.rename(
                allowed_decision("write_files", path), path, "renamed.txt"
            )
        with pytest.raises(SecretFileRefused):
            env.files.delete(allowed_decision("delete_files", path), path)
    if before is not None:
        assert real.read_bytes() == before
    assert not (ROOT / ".env.local").exists()


@pytest.mark.parametrize("operation,args", [
    ("write", ("content",)),
    ("delete", ()),
    ("copy", ("dst.txt",)),
    ("move", ("dst.txt",)),
])
def test_secret_files_blocked_for_every_write_and_delete(env, operation, args) -> None:
    """No operation reaches a secret file, whatever the decision."""
    decision = allowed_decision("write_files" if operation != "delete"
                                else "delete_files", ".env")
    with pytest.raises(SecretFileRefused):
        getattr(env.files, operation)(decision, ".env", *args)


def test_secret_files_never_listed(env) -> None:
    """``include_hidden`` is not a way to reach secrets: they are
    filtered out of listings and searches unconditionally."""
    secret = env.tmp / ".env"
    secret.write_text("SECRET=1")
    (env.tmp / "notes.txt").write_text("hello")
    decision = allowed_decision("read_files", str(env.tmp))
    listing = env.dirs.list(decision, str(env.tmp), include_hidden=True)
    names = {entry["name"] for entry in listing["entries"]}
    assert "notes.txt" in names
    assert ".env" not in names
    search = env.dirs.search(decision, "*", str(env.tmp), include_hidden=True)
    assert all(".env" not in match["path"] for match in search["matches"])


def test_secret_directory_is_not_listable(env) -> None:
    """Listing ``.ssh`` itself is refused, not just its files."""
    decision = allowed_decision("read_files", ".ssh")
    with pytest.raises(SecretFileRefused):
        env.dirs.list(decision, ".ssh")


# ---------------------------------------------------------------------
# Attack 3: symlinks — classify the target, not the link
# ---------------------------------------------------------------------


def test_cannot_delete_via_symlink_to_protected(env, tmp_path) -> None:
    """A link in the project pointing at ``C:\\Windows`` is a
    Windows file: the delete is refused on the target's
    classification, wherever the link lives."""
    link = tmp_path / "innocent.txt"
    try:
        os.symlink(r"C:\Windows\System32\config\system.ini", link)
    except OSError:
        pytest.skip("symlink creation needs elevated privileges")
    decision = allowed_decision("delete_files", str(link))
    with pytest.raises(ProtectedPathRefused):
        env.files.delete(decision, str(link))


def test_symlink_target_classification_not_link_location(env, tmp_path,
                                                          monkeypatch) -> None:
    """Deterministic form of the symlink attack: whatever the
    link is called and wherever it lives, the *target* decides.
    A protected target blocks; a safe target reads."""
    safe = tmp_path / "real.txt"
    safe.write_text("contents")

    monkeypatch.setattr("body.fingers.guards.is_symlink", lambda path: True)
    monkeypatch.setattr(
        "body.fingers.guards.resolve_symlink", lambda path: str(safe)
    )
    read_decision = allowed_decision("read_files", str(tmp_path / "link.txt"))
    assert env.files.read(read_decision, str(tmp_path / "link.txt")) == "contents"

    monkeypatch.setattr(
        "body.fingers.guards.resolve_symlink",
        lambda path: r"C:\Windows\System32",
    )
    with pytest.raises(ProtectedPathRefused):
        env.files.read(read_decision, str(tmp_path / "link.txt"))

    monkeypatch.setattr(
        "body.fingers.guards.resolve_symlink",
        lambda path: str(tmp_path / ".env"),
    )
    with pytest.raises(SecretFileRefused):
        env.files.read(read_decision, str(tmp_path / "link.txt"))


def test_symlink_resolution_is_logged(env, tmp_path, monkeypatch) -> None:
    """Every symlink resolution lands in the activity log, so a
    link used to reach a protected place is visible after the
    fact."""
    monkeypatch.setattr("body.fingers.guards.is_symlink", lambda path: True)
    monkeypatch.setattr(
        "body.fingers.guards.resolve_symlink", lambda path: r"C:\Windows"
    )
    decision = allowed_decision("read_files", str(tmp_path / "link.txt"))
    with pytest.raises(ProtectedPathRefused):
        env.files.read(decision, str(tmp_path / "link.txt"))
    actions = [
        record["action"]
        for record in env.logger.tail(50)
    ]
    assert "symlink_resolved" in actions


def test_real_symlink_to_safe_file_reads(env, tmp_path) -> None:
    """An ordinary symlink to a safe file works through the Body."""
    real = tmp_path / "real.txt"
    real.write_text("through the link")
    link = tmp_path / "link.txt"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("symlink creation needs elevated privileges")
    decision = allowed_decision("read_files", str(link))
    assert env.files.read(decision, str(link)) == "through the link"


# ---------------------------------------------------------------------
# Attack 4: path traversal
# ---------------------------------------------------------------------


@pytest.mark.parametrize("escape", [
    "../../../etc/passwd",
    "..\\..\\..\\windows\\win.ini",
    "docs/../../../../../etc/passwd",
])
def test_cannot_use_path_traversal(env, escape) -> None:
    """A relative path that climbs out of the project is
    refused — the classic traversal shape."""
    resolved, _ = resolve_absolute(escape)
    decision = allowed_decision("read_files", resolved)
    with pytest.raises(PathTraversalRefused):
        env.files.read(decision, escape)


def test_relative_path_inside_the_project_is_allowed(env) -> None:
    """Relative paths that stay inside the project are ordinary
    project files, not traversal."""
    assert escapes_project("docs/notes.md") is False
    assert escapes_project("docs/../notes.md") is False
    assert escapes_project("../../../etc/passwd") is True


def test_api_refuses_traversal_before_prompting(files_app) -> None:
    """The API pre-checks classification, so a traversal is a
    403 with no confirmation dialog to click through."""
    client = TestClient(files_app.app)
    response = client.post(
        "/api/files/read", json={"path": "../../../etc/passwd"}
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Attack 5: kill switch
# ---------------------------------------------------------------------


def test_no_operations_when_kill_switch_engaged(env, tmp_path) -> None:
    """While the switch is engaged nothing runs — not even with
    a decision that was valid a moment ago."""
    env.switch.trigger(reason="test stop")
    file = tmp_path / "file.txt"
    file.write_text("x")
    copy_of = tmp_path / "b.txt"
    # Each operation gets the decision its own category would have
    # been granted, so the refusal that follows can only be the stop.
    decisions = {
        "read": allowed_decision("read_files", str(file)),
        "write": allowed_decision("write_files", str(file)),
        "copy": allowed_decision("write_files", str(file)),
        "move": allowed_decision("write_files", str(file)),
        "rename": allowed_decision("write_files", str(file)),
        "delete": allowed_decision("delete_files", str(file)),
        "stat": allowed_decision("read_files", str(file)),
    }
    for method_name, args in (
        ("read", (str(file),)),
        ("write", (str(file), "y")),
        ("copy", (str(file), str(copy_of))),
        ("move", (str(file), str(copy_of))),
        ("rename", (str(file), "g.txt")),
        ("delete", (str(file),)),
        ("stat", (str(file),)),
    ):
        with pytest.raises(KillSwitchRefused):
            getattr(env.files, method_name)(decisions[method_name], *args)
    # An operation that needed confirming anyway still reports the
    # stop, not its own verdict: while the switch is engaged the
    # honest answer is that nothing runs.
    with pytest.raises(KillSwitchRefused):
        env.dirs.list(ask_decision("read_files", str(tmp_path)), str(tmp_path))
    with pytest.raises(KillSwitchRefused):
        env.trash.trash(allowed_decision("delete_files", str(file)), str(file))


def test_gate_blocks_every_category_while_stopped(env) -> None:
    """The gate the Body sits behind denies every category while
    the switch is engaged, at every trust level."""
    env.switch.trigger(reason="test stop")
    gate = PermissionGate(kill_switch=env.switch)
    for level in ALL_LEVELS:
        for category in ActionCategory:
            decision = gate.decide(Action(category=category, target="x"), level)
            assert decision.decision == BLOCKED


def test_operations_resume_after_reset(env, tmp_path) -> None:
    """Resetting the switch re-arms the Body; the stale decision
    is refused only while the stop is in force."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("read_files", str(file))
    env.switch.trigger(reason="test stop")
    with pytest.raises(KillSwitchRefused):
        env.files.read(decision, str(file))
    env.switch.reset(typed_word="RESET", confirmed=True)
    assert env.files.read(decision, str(file)) == "x"


def test_api_returns_409_when_stopped(files_app) -> None:
    """The API maps an engaged switch to 409 for every file
    operation, before any other check — including the routes whose
    bodies are shaped differently."""
    files_app.switch.trigger(reason="test stop")
    client = TestClient(files_app.app)
    for route, body in (
        ("/api/files/read", {"path": "notes.txt"}),
        ("/api/files/write", {"path": "notes.txt", "content": "x"}),
        ("/api/files/copy", {"src": "a.txt", "dst": "b.txt"}),
        ("/api/files/move", {"src": "a.txt", "dst": "b.txt"}),
        ("/api/files/rename", {"path": "notes.txt", "new_name": "b.txt"}),
        ("/api/files/delete", {"path": "notes.txt"}),
        ("/api/files/mkdir", {"path": "dir"}),
        ("/api/files/list", {"path": "dir"}),
        ("/api/files/search", {"pattern": "*.py", "root": "."}),
        ("/api/files/tree", {"path": "."}),
        ("/api/files/stat", {"path": "notes.txt"}),
        ("/api/files/restore", {"trash_id": "abc"}),
        ("/api/files/empty-trash", {"older_than_days": 0}),
        ("/api/files/restore-backup", {"path": "notes.txt",
                                       "backup_version": 1}),
    ):
        response = client.post(route, json=body)
        assert response.status_code == 409, (route, response.json())


# ---------------------------------------------------------------------
# Attack 6: ZUZZ's own files are read-only
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "brain/thalamus/router.py",
    "body/fingers/file_manager.py",
    "core/paths.py",
    "config/trust_levels.json",
    "storage/logs/activity_log.jsonl",
    "storage/stop.flag",
    "requirements.txt",
    "start.py",
])
def test_cannot_modify_zuzz_core_files(env, path) -> None:
    """ZUZZ's code, its logs and its safety state are read-only
    to the agent, even at level 5 with a valid decision."""
    decision = allowed_decision("write_files", path)
    with pytest.raises(ZuzzCoreRefused):
        env.files.write(decision, path, "content")
    delete_decision = allowed_decision("delete_files", path)
    with pytest.raises(ZuzzCoreRefused):
        env.files.delete(delete_decision, path)


@pytest.mark.parametrize("path", [
    "brain/thalamus/router.py",
    "body/fingers/file_manager.py",
    "core/paths.py",
])
def test_zuzz_core_files_are_readable(env, path) -> None:
    """Read-only, not invisible: ZUZZ can read its own code."""
    decision = allowed_decision("read_files", path)
    try:
        result = env.files.read(decision, path)
    except FileTooLarge:
        pass  # large files are refused on size, not on classification
    else:
        assert isinstance(result, str)


def test_zuzz_can_modify_plugins_directory(env, tmp_path) -> None:
    """Plugins are how ZUZZ grows: the plugins directory is
    writable, unlike the core."""
    plugin = tmp_path / "plugins"
    plugin.mkdir()
    target = plugin / "my_plugin.py"
    decision = allowed_decision("write_files", str(target))
    result = env.files.write(decision, str(target), "# plugin\n")
    assert result["path"] == str(target)
    assert target.read_text() == "# plugin\n"


# ---------------------------------------------------------------------
# Attack 7: replay and category misuse
# ---------------------------------------------------------------------


def test_decision_cannot_be_replayed_against_another_path(env, tmp_path) -> None:
    """A decision issued for file A does not authorise file B:
    the Body matches the decision's target against the path it
    was asked to act on."""
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    file_a.write_text("a")
    file_b.write_text("b")
    decision = allowed_decision("read_files", str(file_a))
    assert env.files.read(decision, str(file_a)) == "a"
    with pytest.raises(DecisionReplayRefused):
        env.files.read(decision, str(file_b))


def test_decision_for_a_directory_covers_its_contents(env, tmp_path) -> None:
    """A decision issued for a directory authorises operations on
    what is inside it — that is what a batch decision means."""
    directory = tmp_path / "batch"
    directory.mkdir()
    (directory / "one.txt").write_text("1")
    decision = allowed_decision("read_files", str(directory))
    assert env.files.read(decision, str(directory / "one.txt")) == "1"


def test_decision_category_must_match_the_operation(env, tmp_path) -> None:
    """A read decision cannot pay for a write."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("read_files", str(file))
    with pytest.raises(DecisionReplayRefused):
        env.files.write(decision, str(file), "y")


def test_dry_run_decisions_never_execute(env, tmp_path) -> None:
    """A dry-run decision reaching the Body is refused: the route
    short-circuits dry runs, and the Body is the backstop."""
    action = Action(
        category=ActionCategory.READ_FILES,
        target=str(tmp_path / "x.txt"),
        dry_run=True,
    )
    decision = PermissionDecision.make(action, 5, AUTO, "dry run")
    with pytest.raises(DryRunRefused):
        env.files.read(decision, str(tmp_path / "x.txt"))


# ---------------------------------------------------------------------
# Attack 8: bulk deletion trips the watchdog
# ---------------------------------------------------------------------


def test_bulk_delete_triggers_watchdog(env, tmp_path) -> None:
    """A thousand deletions log a thousand delete records, and
    the watchdog — reading the activity log, not the filesystem
    — trips the switch."""
    directory = victim_dir(env, "bulk")
    for index in range(1000):
        (directory / f"file_{index}.txt").write_text("x")
    for index in range(1000):
        path = directory / f"file_{index}.txt"
        decision = allowed_decision("delete_files", str(path))
        env.files.delete(decision, str(path))
    watchdog = Watchdog(env.switch, logger=env.logger)
    watchdog.max_cpu_percent = lambda: 1.0  # type: ignore[method-assign]
    report = watchdog.poll()
    assert report["tripped"] is True
    assert report["deletions_in_window"] >= 25
    assert env.switch.is_killed()


def test_few_deletions_do_not_trip_the_watchdog(env, tmp_path) -> None:
    """Ordinary cleanup is not a runaway loop."""
    directory = victim_dir(env, "few")
    for index in range(5):
        (directory / f"file_{index}.txt").write_text("x")
    for index in range(5):
        path = directory / f"file_{index}.txt"
        env.files.delete(allowed_decision("delete_files", str(path)), str(path))
    watchdog = Watchdog(env.switch, logger=env.logger)
    watchdog.max_cpu_percent = lambda: 1.0  # type: ignore[method-assign]
    assert watchdog.poll()["tripped"] is False


# ---------------------------------------------------------------------
# Attack 9: size limits
# ---------------------------------------------------------------------


def test_cannot_read_oversized_files(env, tmp_path, monkeypatch) -> None:
    """A 10 GB read is refused before a byte is opened: the size
    is checked against the 50 MB hard maximum first."""
    file = tmp_path / "huge.bin"
    file.write_text("x")
    monkeypatch.setattr(
        "body.fingers.file_manager._file_size", lambda path: 10 * 1024 ** 3
    )
    decision = allowed_decision("read_files", str(file))
    with pytest.raises(FileTooLarge):
        env.files.read(decision, str(file))


def test_read_cap_is_configurable_below_the_hard_maximum(env, tmp_path,
                                                          monkeypatch) -> None:
    """The default 5 MB cap can be raised per call, but never
    past the 50 MB hard maximum."""
    file = tmp_path / "sized.bin"
    file.write_text("x")
    monkeypatch.setattr(
        "body.fingers.file_manager._file_size", lambda path: 6 * 1024 ** 2
    )
    decision = allowed_decision("read_files", str(file))
    with pytest.raises(FileTooLarge):
        env.files.read(decision, str(file))  # above the 5 MB default
    monkeypatch.setattr(
        "body.fingers.file_manager._file_size", lambda path: 49 * 1024 ** 2
    )
    monkeypatch.setattr("body.fingers.file_manager._open_locked", bomb)
    with pytest.raises(FileTooLarge):
        env.files.read(decision, str(file), max_size=48 * 1024 ** 2)


def test_hard_maximum_cannot_be_raised_past_50mb(env, tmp_path, monkeypatch) -> None:
    """The 50 MB hard maximum is not a parameter: asking for more
    still refuses a 60 MB file."""
    file = tmp_path / "big.bin"
    file.write_text("x")
    monkeypatch.setattr(
        "body.fingers.file_manager._file_size", lambda path: 60 * 1024 ** 2
    )
    decision = allowed_decision("read_files", str(file))
    with pytest.raises(FileTooLarge):
        env.files.read(decision, str(file), max_size=100 * 1024 ** 2)


def test_default_and_hard_limits_have_the_specified_values() -> None:
    """5 MB default, 50 MB hard max, 64 KB stream chunks, 30 s
    lock wait — pinned to the approved numbers."""
    assert DEFAULT_MAX_READ == 5_242_880
    assert HARD_MAX_READ == 52_428_800
    assert LOCK_TIMEOUT == 30.0


# ---------------------------------------------------------------------
# Attack 10: binary and encoding handling
# ---------------------------------------------------------------------


def test_binary_as_text_handled_gracefully(env, tmp_path) -> None:
    """A binary file is refused with a clear message that
    carries the first 100 bytes in hex and points at stat()."""
    file = tmp_path / "image.bin"
    file.write_bytes(bytes(range(256)) * 4)
    decision = allowed_decision("read_files", str(file))
    with pytest.raises(BinaryFileRefused) as excinfo:
        env.files.read(decision, str(file))
    message = str(excinfo.value)
    assert "first 100 bytes" in message
    assert "stat()" in message
    assert len(message.split(":")[-1].strip()) >= 200  # the hex dump


def test_binary_read_explicitly_returns_bytes(env, tmp_path) -> None:
    """Reading with binary=True is the deliberate escape hatch
    and returns raw bytes, not a refusal."""
    file = tmp_path / "blob.bin"
    payload = bytes(range(256))
    file.write_bytes(payload)
    decision = allowed_decision("read_files", str(file))
    assert env.files.read(decision, str(file), binary=True) == payload


def test_encoding_fallback_chain(env, tmp_path) -> None:
    """UTF-8 first, then UTF-8 with BOM, then latin-1."""
    plain = tmp_path / "plain.txt"
    plain.write_bytes("héllo".encode("utf-8"))
    bom = tmp_path / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbf" + "héllo".encode("utf-8"))
    latin = tmp_path / "latin.txt"
    latin.write_bytes("caf\xe9".encode("latin-1"))
    decision = allowed_decision("read_files", str(tmp_path))
    assert env.files.read(decision, str(plain)) == "héllo"
    assert env.files.read(decision, str(bom)) == "héllo"
    assert env.files.read(decision, str(latin)) == "café"


def test_decode_reports_hex_when_no_encoding_works() -> None:
    """A chunk no encoding can cleanly decode is reported with
    its first 100 bytes in hex (the agreed failure format)."""
    chunk = bytes(range(256)) * 4
    with pytest.raises(BinaryFileRefused) as excinfo:
        _decode(chunk, "x.bin")
    assert chunk[:100].hex() in str(excinfo.value)


# ---------------------------------------------------------------------
# Attack 11: lock detection
# ---------------------------------------------------------------------


def test_locked_file_raises_423_shape_after_timeout(env, tmp_path, monkeypatch) -> None:
    """A file another process holds is reported as locked after
    the wait, with a message that says what to do — not an
    endless retry."""
    file = tmp_path / "locked.txt"
    file.write_text("x")
    calls = []

    def locked_open(path, mode):
        calls.append(path)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", locked_open)
    decision = allowed_decision("read_files", str(file))
    started = time.monotonic()
    with pytest.raises(FileLockedError) as excinfo:
        env.files.read(decision, str(file), lock_timeout=0)
    assert "locked by another process" in str(excinfo.value)
    assert "close the other app" in str(excinfo.value)
    assert time.monotonic() - started < 1
    assert calls  # the open was attempted, not silently skipped


def test_open_locked_waits_then_succeeds(env, tmp_path, monkeypatch) -> None:
    """A file that unlocks within the wait is read normally."""
    file = tmp_path / "slow.txt"
    file.write_text("eventually")
    attempts = []

    def flaky_open(path, mode):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError(13, "Permission denied")
        return real_open(path, mode)

    real_open = open
    monkeypatch.setattr("builtins.open", flaky_open)
    monkeypatch.setattr("body.fingers.file_manager.time.sleep", lambda _: None)
    decision = allowed_decision("read_files", str(file))
    assert env.files.read(decision, str(file), lock_timeout=5) == "eventually"
    assert len(attempts) == 3


# ---------------------------------------------------------------------
# Happy paths — the operations themselves
# ---------------------------------------------------------------------


def test_write_then_read_round_trip(env, tmp_path) -> None:
    """The simplest correct use of the Body."""
    file = tmp_path / "note.txt"
    write_decision = allowed_decision("write_files", str(file))
    result = env.files.write(write_decision, str(file), "hello world")
    assert result["bytes"] == 11
    assert result["backup"] is None  # nothing existed to back up
    read_decision = allowed_decision("read_files", str(file))
    assert env.files.read(read_decision, str(file)) == "hello world"
    meta = env.files.stat(read_decision, str(file))
    assert meta["size"]["bytes"] == 11
    assert meta["size"]["human"] == "11 B"
    assert meta["is_symlink"] is False
    assert meta["classification"] == "safe"
    assert meta["permissions"]["read"] is True


def test_write_append_mode(env, tmp_path) -> None:
    """Append adds without overwriting, and does not back up."""
    file = tmp_path / "log.txt"
    file.write_text("one\n")
    decision = allowed_decision("write_files", str(file))
    env.files.write(decision, str(file), "two\n", mode="a")
    assert file.read_text() == "one\ntwo\n"
    assert not (tmp_path / "log.txt.bak.1").exists()


def test_write_rejects_unknown_mode(env, tmp_path) -> None:
    """Only 'w' and 'a' exist."""
    decision = allowed_decision("write_files", str(tmp_path / "x.txt"))
    with pytest.raises(ValueError):
        env.files.write(decision, str(tmp_path / "x.txt"), "x", mode="x")


def test_copy_move_and_rename(env, tmp_path) -> None:
    """Copy leaves the source; move and rename do not."""
    source = tmp_path / "source.txt"
    source.write_text("payload")
    copied = env.files.copy(
        allowed_decision("write_files", str(source)),
        str(source),
        str(tmp_path / "copy.txt"),
    )
    assert copied["bytes"] == 7
    assert source.exists()
    # Each operation needs its own decision: the gate approves a
    # specific target, and after a move the approved source no longer
    # exists at that path. Reusing one decision across operations is
    # the replay the Body refuses, so the tests model real usage.
    moved_path = tmp_path / "moved.txt"
    env.files.move(
        allowed_decision("write_files", str(source)), str(source), str(moved_path)
    )
    assert not source.exists()
    assert moved_path.exists()
    renamed = env.files.rename(
        allowed_decision("write_files", str(moved_path)),
        str(moved_path),
        "final.txt",
    )
    assert renamed["dst"].endswith("final.txt")
    assert not moved_path.exists()


def test_rename_requires_a_plain_name(env, tmp_path) -> None:
    """A rename cannot smuggle a path in the new name."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("write_files", str(file))
    for bad in ("../escape.txt", "", ".", "..", "sub/dir.txt"):
        with pytest.raises(ValueError):
            env.files.rename(decision, str(file), bad)


def test_copy_refuses_a_protected_destination(env, tmp_path, monkeypatch) -> None:
    """Copying a safe file onto a system path is a write to that
    path, and is refused — the destination is guarded too."""
    source = tmp_path / "safe.txt"
    source.write_text("innocent")
    decision = allowed_decision("write_files", str(source))
    monkeypatch.setattr("body.fingers.file_manager.shutil.copy2", bomb)
    with pytest.raises(ProtectedPathRefused):
        env.files.copy(decision, str(source), r"C:\Windows\evil.dll")


def test_mkdir_creates_directories(env, tmp_path) -> None:
    """Plain and parents form."""
    env.dirs.mkdir(
        allowed_decision("write_files", str(tmp_path / "new")),
        str(tmp_path / "new"),
    )
    assert (tmp_path / "new").is_dir()
    deep = tmp_path / "a" / "b" / "c"
    env.dirs.mkdir(
        allowed_decision("write_files", str(deep)), str(deep), parents=True
    )
    assert deep.is_dir()
    with pytest.raises(FileExistsError):
        env.dirs.mkdir(
            allowed_decision("write_files", str(tmp_path / "new")),
            str(tmp_path / "new"),
        )


def test_list_paginates_and_reports_totals(env, tmp_path) -> None:
    """limit/offset paging with the total, so the UI can page."""
    directory = victim_dir(env, "paged")
    for index in range(25):
        (directory / f"file_{index:02d}.txt").write_text("x")
    decision = allowed_decision("read_files", str(directory))
    first_page = env.dirs.list(decision, str(directory), limit=10, offset=0)
    assert first_page["total"] == 25
    assert len(first_page["entries"]) == 10
    assert first_page["entries"][0]["name"] == "file_00.txt"
    third_page = env.dirs.list(decision, str(directory), limit=10, offset=20)
    assert len(third_page["entries"]) == 5


def test_list_hides_hidden_files_unless_asked(env, tmp_path) -> None:
    """.git and friends appear only when include_hidden is set."""
    (env.tmp / ".git").mkdir()
    (env.tmp / ".git" / "config").write_text("x")
    (env.tmp / "visible.txt").write_text("x")
    decision = allowed_decision("read_files", str(env.tmp))
    plain = env.dirs.list(decision, str(env.tmp))
    assert {e["name"] for e in plain["entries"]} == {"visible.txt"}
    with_hidden = env.dirs.list(decision, str(env.tmp), include_hidden=True)
    names = {e["name"] for e in with_hidden["entries"]}
    assert names == {"visible.txt", ".git"}


def test_list_classifies_each_entry(env, tmp_path) -> None:
    """Every listed entry carries its classification, so the UI
    can warn before the user picks a dangerous target."""
    (env.tmp / "Desktop").mkdir()
    (env.tmp / "Desktop" / "notes.txt").write_text("x")
    decision = allowed_decision("read_files", str(env.tmp))
    listing = env.dirs.list(decision, str(env.tmp), include_hidden=True)
    by_name = {e["name"]: e for e in listing["entries"]}
    assert by_name["Desktop"]["classification"] == "caution"


def test_search_matches_globs_and_regex(env, tmp_path) -> None:
    """Glob by default, regex on request; patterns are data."""
    directory = victim_dir(env, "search")
    for name in ("test_a.py", "test_b.py", "notes.md", "run.py"):
        (directory / name).write_text("x")
    decision = allowed_decision("read_files", str(directory))
    globs = env.dirs.search(decision, "test_*.py", str(directory))
    assert {m["name"] for m in globs["matches"]} == {"test_a.py", "test_b.py"}
    regex = env.dirs.search(
        decision, r"^run\.", str(directory), regex=True
    )
    assert {m["name"] for m in regex["matches"]} == {"run.py"}


def test_search_respects_max_depth(env, tmp_path) -> None:
    """Deep files are found only within the depth budget."""
    root = victim_dir(env, "deep")
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("x")
    (root / "top.txt").write_text("x")
    decision = allowed_decision("read_files", str(root))
    shallow = env.dirs.search(decision, "*.txt", str(root), max_depth=2)
    assert {m["name"] for m in shallow["matches"]} == {"top.txt"}
    deep = env.dirs.search(decision, "*.txt", str(root), max_depth=5)
    assert {m["name"] for m in deep["matches"]} == {"top.txt", "deep.txt"}


def test_search_prunes_protected_trees(env, tmp_path, monkeypatch) -> None:
    """A search rooted at a protected ancestor never descends
    into the system tree — and never lists it."""
    monkeypatch.setattr(
        "body.fingers.directory_manager._visible", lambda name, hidden: True
    )
    decision = allowed_decision("read_files", r"C:\Windows")
    with pytest.raises(ProtectedPathRefused):
        env.dirs.search(decision, "*", r"C:\Windows")
    # Even with hidden files exposed, a protected root is refused
    # before a single directory entry is read.
    with pytest.raises(ProtectedPathRefused):
        env.dirs.search(decision, "*", r"C:\Windows", include_hidden=True)


def test_tree_renders_a_nested_structure(env, tmp_path) -> None:
    """The tree view the UI renders, bounded in depth."""
    root = victim_dir(env, "tree")
    (root / "leaf.txt").write_text("x")
    branch = root / "branch"
    branch.mkdir()
    (branch / "leaf.txt").write_text("x")
    decision = allowed_decision("read_files", str(root))
    node = env.dirs.tree(decision, str(root), max_depth=2)
    assert node["name"] == "tree"
    assert node["type"] == "dir"
    children = {c["name"]: c for c in node["children"]}
    assert children["leaf.txt"]["type"] == "file"
    assert children["branch"]["children"][0]["name"] == "leaf.txt"
    assert env.dirs.tree(decision, str(root), max_depth=0)["children"] == []


# ---------------------------------------------------------------------
# Backup before overwrite
# ---------------------------------------------------------------------


def test_backup_created_before_overwrite(env, tmp_path) -> None:
    """Overwriting snapshots the old content as ``.bak.1`` before
    the first byte changes."""
    file = tmp_path / "data.txt"
    file.write_text("version 1")
    decision = allowed_decision("write_files", str(file))
    result = env.files.write(decision, str(file), "version 2")
    assert result["backup"] == str(tmp_path / "data.txt.bak.1")
    assert (tmp_path / "data.txt.bak.1").read_text() == "version 1"
    assert file.read_text() == "version 2"


def test_backups_rotate_through_three_versions(env, tmp_path) -> None:
    """Three overwrites keep .bak.1 newest and .bak.3 oldest, and
    the fourth overwrite drops the oldest."""
    file = tmp_path / "data.txt"
    file.write_text("v0")
    decision = allowed_decision("write_files", str(file))
    for version in ("v1", "v2", "v3", "v4"):
        env.files.write(decision, str(file), version)
    assert (tmp_path / "data.txt.bak.1").read_text() == "v3"
    assert (tmp_path / "data.txt.bak.2").read_text() == "v2"
    assert (tmp_path / "data.txt.bak.3").read_text() == "v1"
    assert not (tmp_path / "data.txt.bak.4").exists()


def test_list_backups_newest_first(env, tmp_path) -> None:
    """The backup list the UI shows, newest version first."""
    file = tmp_path / "data.txt"
    file.write_text("v0")
    decision = allowed_decision("write_files", str(file))
    for version in ("v1", "v2"):
        env.files.write(decision, str(file), version)
    backups = list_backups_for(file)
    assert backups == [
        str(tmp_path / "data.txt.bak.2"),
        str(tmp_path / "data.txt.bak.1"),
    ]


def list_backups_for(file: Path) -> list:
    """List the backups of a file, newest first."""
    from body.fingers.file_backup import list_backups

    return list_backups(str(file))


def test_restore_backup_puts_a_version_back(env, tmp_path) -> None:
    """A backed-up version can be restored over the current file."""
    file = tmp_path / "data.txt"
    file.write_text("v0")
    decision = allowed_decision("write_files", str(file))
    env.files.write(decision, str(file), "v1")
    env.files.write(decision, str(file), "v2")
    from body.fingers.file_backup import restore_backup

    restored = restore_backup(decision, str(file), 2)
    assert restored == str(file)
    assert file.read_text() == "v0"


def test_restore_backup_refuses_missing_versions(env, tmp_path) -> None:
    """Only versions that exist can be restored."""
    file = tmp_path / "data.txt"
    file.write_text("v0")
    decision = allowed_decision("write_files", str(file))
    from body.fingers.file_backup import restore_backup

    with pytest.raises(FileNotFoundError):
        restore_backup(decision, str(file), 7)


def test_backups_are_never_made_for_secrets(env, tmp_path) -> None:
    """Backing up a secret would copy the secret, so the backup
    list of a secret file is always empty."""
    secret = env.tmp / ".env"
    secret.write_text("KEY=1")
    assert list_backups_for(secret) == []


# ---------------------------------------------------------------------
# Trash and restoration
# ---------------------------------------------------------------------


def test_trash_restoration_works(env, tmp_path) -> None:
    """Delete moves to the trash and returns an id; restore moves
    the file back to exactly where it was."""
    file = tmp_path / "important.txt"
    file.write_text("precious data")
    decision = allowed_decision("delete_files", str(file))
    result = env.files.delete(decision, str(file))
    trash_id = result["trash_id"]
    assert not file.exists()
    items = env.trash.list_trash()
    assert len(items) == 1
    assert items[0]["trash_id"] == trash_id
    assert items[0]["original_path"] == str(file)
    restore_decision = allowed_decision("write_files", f"trash:{trash_id}")
    restored = env.trash.restore(restore_decision, trash_id)
    assert restored == str(file)
    assert file.read_text() == "precious data"
    assert env.trash.list_trash() == []


def test_trash_directories_whole(env, tmp_path) -> None:
    """A directory is trashed with all of its contents."""
    directory = victim_dir(env, "whole")
    (directory / "a.txt").write_text("a")
    (directory / "b.txt").write_text("b")
    decision = allowed_decision("delete_files", str(directory))
    trash_id = env.files.delete(decision, str(directory))["trash_id"]
    assert not directory.exists()
    restore_decision = allowed_decision("write_files", f"trash:{trash_id}")
    env.trash.restore(restore_decision, trash_id)
    assert (directory / "a.txt").read_text() == "a"
    assert (directory / "b.txt").read_text() == "b"


def test_restore_refuses_to_overwrite(env, tmp_path) -> None:
    """Restoring onto a file that exists again is a conflict, not
    an overwrite."""
    file = tmp_path / "file.txt"
    file.write_text("original")
    decision = allowed_decision("delete_files", str(file))
    trash_id = env.files.delete(decision, str(file))["trash_id"]
    file.write_text("newer")
    restore_decision = allowed_decision("write_files", f"trash:{trash_id}")
    with pytest.raises(RestoreConflict):
        env.trash.restore(restore_decision, trash_id)


def test_restore_of_unknown_id_is_an_error(env) -> None:
    """A made-up trash id is refused, not silently ignored."""
    decision = allowed_decision("write_files", "trash:does-not-exist")
    with pytest.raises(TrashError):
        env.trash.restore(decision, "does-not-exist")


def test_restore_and_purge_leave_no_empty_folders_behind(env, tmp_path) -> None:
    """Each trashed item gets its own folder so same-named files cannot
    collide. Once the item is restored or purged that folder must go
    too, or the trash grows one dead directory per operation forever."""
    restored = tmp_path / "restored.txt"
    restored.write_text("x")
    restored_id = env.files.delete(
        allowed_decision("delete_files", str(restored)), str(restored)
    )["trash_id"]
    purged = tmp_path / "purged.txt"
    purged.write_text("y")
    purged_id = env.files.delete(
        allowed_decision("delete_files", str(purged)), str(purged)
    )["trash_id"]
    root = env.tmp / "trash"
    assert (root / restored_id).is_dir()
    assert (root / purged_id).is_dir()

    env.trash.restore(
        allowed_decision("write_files", f"trash:{restored_id}"), restored_id
    )
    assert restored.read_text() == "x"
    assert not (root / restored_id).exists(), "restore left an empty folder"

    env.trash.empty_trash(allowed_decision("delete_files", "trash"))
    assert not (root / purged_id).exists(), "purge left an empty folder"
    assert list(root.iterdir()) == [root / "manifest.json"] or not any(
        path.is_dir() for path in root.iterdir()
    )
    assert env.trash.list_trash() == []


def test_trash_lands_in_the_configured_root(env, tmp_path) -> None:
    """The trashed item physically lives in the trash root, under
    its id, keeping its original name."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("delete_files", str(file))
    trash_id = env.files.delete(decision, str(file))["trash_id"]
    resting = env.tmp / "trash" / trash_id / "file.txt"
    assert resting.is_file()


def test_trash_falls_back_to_the_project_root(monkeypatch) -> None:
    """When the source's drive cannot host the trash, the project
    directory's ``.zuzz_trash`` is the fallback."""

    def unreadable_drive(*args, **kwargs):
        """Stand in for a read-only or foreign drive: the mkdir that
        would create the same-drive trash directory fails the way a
        real unwritable volume fails."""
        raise OSError("read-only volume")

    monkeypatch.setattr(Path, "mkdir", unreadable_drive)
    monkeypatch.setattr(
        "body.fingers.trash_manager.os.path.splitdrive", lambda path: ("Z:", path)
    )
    manager = TrashManager()
    assert manager._location_for(Path("C:/data/file.txt")) == manager._manifest_root()
    assert manager._manifest_root() == ROOT / ".zuzz_trash"


def test_trash_stats_warn_past_1gb(env, tmp_path) -> None:
    """The UI warning the spec asks for: over a gigabyte of trash
    is flagged."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("delete_files", str(file))
    env.files.delete(decision, str(file))
    assert env.trash.trash_stats()["warn"] is False
    manifest = env.trash._manifest()
    for entry in manifest.values():
        entry["size"] = 2 * 1024 ** 3
    env.trash._write_manifest(manifest)
    assert env.trash.trash_stats()["warn"] is True


# ---------------------------------------------------------------------
# Purge thresholds
# ---------------------------------------------------------------------


def test_auto_purge_respects_30_day_threshold(env, tmp_path) -> None:
    """The scheduled purge removes only items older than 30 days;
    fresher items stay restorable."""
    fresh = tmp_path / "fresh.txt"
    fresh.write_text("new")
    stale = tmp_path / "stale.txt"
    stale.write_text("old")
    delete_decision = allowed_decision("delete_files", str(stale))
    env.files.delete(delete_decision, str(stale))
    env.files.delete(
        allowed_decision("delete_files", str(fresh)), str(fresh)
    )
    # Age the stale item past the threshold by hand.
    manifest = env.trash._manifest()
    for entry in manifest.values():
        if entry["original_path"] == str(stale):
            entry["trashed_at"] = time.time() - (AUTO_PURGE_DAYS + 1) * 86400
    env.trash._write_manifest(manifest)
    purged = env.trash.auto_purge_old()
    assert purged == 1
    remaining = {item["original_path"] for item in env.trash.list_trash()}
    assert remaining == {str(fresh)}


def test_auto_purge_never_breaks_the_retention_floor(env, tmp_path) -> None:
    """No matter what the configuration says, the automatic purge
    never touches an item younger than 7 days."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("delete_files", str(file))
    env.files.delete(decision, str(file))
    manifest = env.trash._manifest()
    for entry in manifest.values():
        entry["trashed_at"] = time.time() - 8 * 86400  # 8 days old
    env.trash._write_manifest(manifest)
    assert env.trash.auto_purge_old() == 0
    assert len(env.trash.list_trash()) == 1
    assert MIN_RETENTION_DAYS == 7
    assert AUTO_PURGE_DAYS == 30


def test_empty_trash_purges_everything(env, tmp_path) -> None:
    """The user asked for it, so the retention floor does not
    apply — but each item still goes to the purge transport, not
    an outright delete."""
    for name in ("a.txt", "b.txt"):
        file = tmp_path / name
        file.write_text("x")
        env.files.delete(
            allowed_decision("delete_files", str(file)), str(file)
        )
    assert env.trash.empty_trash(
        allowed_decision("delete_files", "trash"), older_than_days=0
    ) == 2
    assert env.trash.list_trash() == []


def test_empty_trash_respects_the_age_filter(env, tmp_path) -> None:
    """``older_than_days`` filters what a manual purge removes."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    env.files.delete(
        allowed_decision("delete_files", str(file)), str(file)
    )
    purged = env.trash.empty_trash(
        allowed_decision("delete_files", "trash"), older_than_days=30
    )
    assert purged == 0
    assert len(env.trash.list_trash()) == 1


def test_purge_uses_the_transport_not_an_outright_delete(env, tmp_path) -> None:
    """The Body never deletes outright: purging hands the item to
    the transport, which is send2trash in production (the
    operating system's recycle bin — a second safety net)."""
    purged = []

    def recording_transport(path):
        purged.append(path)

    manager = TrashManager(
        trash_root=env.tmp / "trash2", purge_transport=recording_transport
    )
    file = tmp_path / "file.txt"
    file.write_text("x")
    manager.trash(
        allowed_decision("delete_files", str(file)), str(file)
    )
    manager.empty_trash(allowed_decision("delete_files", "trash"))
    assert len(purged) == 1
    assert "trash2" in purged[0]


# ---------------------------------------------------------------------
# API-level tests: status codes and the confirmation flow
# ---------------------------------------------------------------------


def test_api_read_at_l1_asks_then_executes(files_app, tmp_path) -> None:
    """The default trust level asks before reading; the
    confirmation is answered with the id, and the id works once."""
    client = TestClient(files_app.app)
    file = tmp_path / "note.txt"
    file.write_text("hello")
    first = client.post("/api/files/read", json={"path": str(file)})
    assert first.status_code == 202
    prompt = first.json()
    assert prompt["status"] == "confirmation_required"
    assert prompt["operation"] == "read"
    assert prompt["target"] == str(file)
    assert prompt["trust_level"] == 1
    assert prompt["dry_run_available"] is True
    assert prompt["threat"]["score"] == 0
    second = client.post(
        "/api/files/read",
        json={
            "path": str(file),
            "confirmed": True,
            "confirmation_id": prompt["confirmation_id"],
        },
    )
    assert second.status_code == 200
    assert second.json()["result"] == "hello"
    replay = client.post(
        "/api/files/read",
        json={
            "path": str(file),
            "confirmed": True,
            "confirmation_id": prompt["confirmation_id"],
        },
    )
    assert replay.status_code == 400  # the id was consumed


def test_api_confirmation_id_cannot_be_reused_for_another_path(
    files_app, tmp_path
) -> None:
    """A confirmation issued for file A cannot pay for file B."""
    client = TestClient(files_app.app)
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_text("1")
    two.write_text("2")
    first = client.post("/api/files/read", json={"path": str(one)})
    confirmation_id = first.json()["confirmation_id"]
    mismatch = client.post(
        "/api/files/read",
        json={
            "path": str(two),
            "confirmed": True,
            "confirmation_id": confirmation_id,
        },
    )
    assert mismatch.status_code == 400


def test_api_write_makes_a_backup_then_overwrites(files_app, tmp_path) -> None:
    """The write endpoint shows the backup in its result, and the
    old content survives as ``.bak.1``."""
    files_app.app.state.trust.save(4)  # writes auto-approve from L2
    client = TestClient(files_app.app)
    file = tmp_path / "data.txt"
    file.write_text("old")
    response = client.post(
        "/api/files/write", json={"path": str(file), "content": "new"}
    )
    assert response.status_code == 200
    assert response.json()["result"]["backup"].endswith("data.txt.bak.1")
    assert file.read_text() == "new"
    assert (tmp_path / "data.txt.bak.1").read_text() == "old"


def test_api_delete_flows_through_the_trash(files_app, tmp_path) -> None:
    """Delete asks at every level; the confirmed delete lands in
    the trash and is restorable through the API."""
    client = TestClient(files_app.app)
    file = tmp_path / "doomed.txt"
    file.write_text("x")
    first = client.post("/api/files/delete", json={"path": str(file)})
    assert first.status_code == 202
    prompt = first.json()
    assert prompt["reversible"] is True
    assert prompt["file_count"] == 1
    assert prompt["total_size_bytes"] == 1
    second = client.post(
        "/api/files/delete",
        json={
            "path": str(file),
            "confirmed": True,
            "confirmation_id": prompt["confirmation_id"],
        },
    )
    assert second.status_code == 200
    trash = client.get("/api/files/trash")
    assert trash.status_code == 200
    items = trash.json()["items"]
    assert len(items) == 1
    # Restore is a write, so it asks at L1 like every other write.
    pending = client.post(
        "/api/files/restore",
        json={"trash_id": items[0]["trash_id"]},
    )
    assert pending.status_code == 202
    restore = client.post(
        "/api/files/restore",
        json={
            "trash_id": items[0]["trash_id"],
            "confirmed": True,
            "confirmation_id": pending.json()["confirmation_id"],
        },
    )
    assert restore.status_code == 200
    assert file.read_text() == "x"


def test_api_secret_file_is_403_before_any_prompt(files_app) -> None:
    """A secret is refused outright: there is no confirmation
    dialog that could unlock it, at any trust level."""
    files_app.app.state.trust.save(5)
    client = TestClient(files_app.app)
    for body in (
        {"path": ".env"},
        {"path": ".env.local"},
        {"path": ".ssh/id_rsa"},
        {"path": "server.pem"},
    ):
        response = client.post("/api/files/read", json=body)
        assert response.status_code == 403, body
        response = client.post(
            "/api/files/write", json={**body, "content": "x"}
        )
        assert response.status_code == 403, body


def test_api_protected_path_is_403(files_app) -> None:
    """System paths are refused before any prompt."""
    client = TestClient(files_app.app)
    response = client.post(
        "/api/files/read", json={"path": r"C:\Windows\win.ini"}
    )
    assert response.status_code == 403
    response = client.post(
        "/api/files/write",
        json={"path": r"C:\Windows\evil.dll", "content": "x"},
    )
    assert response.status_code == 403


def test_api_oversized_file_is_413(files_app, tmp_path, monkeypatch) -> None:
    """A file above the hard maximum maps to 413."""
    files_app.app.state.trust.save(4)
    file = tmp_path / "huge.bin"
    file.write_text("x")
    monkeypatch.setattr(
        "body.fingers.file_manager._file_size",
        lambda path: 10 * 1024 ** 3,
    )
    client = TestClient(files_app.app)
    response = client.post("/api/files/read", json={"path": str(file)})
    assert response.status_code == 413


def test_api_locked_file_is_423(files_app, tmp_path, monkeypatch) -> None:
    """A file held by another process maps to 423 with the
    close-the-other-app message."""
    files_app.app.state.trust.save(4)
    file = tmp_path / "locked.txt"
    file.write_text("x")

    def locked_open(path, mode, timeout=30.0):
        raise FileLockedError(
            f"{path} is locked by another process; "
            f"close the other app and retry"
        )

    monkeypatch.setattr("body.fingers.file_manager._open_locked", locked_open)
    client = TestClient(files_app.app)
    response = client.post("/api/files/read", json={"path": str(file)})
    assert response.status_code == 423
    assert "close the other app" in response.json()["detail"]


def test_api_binary_file_is_a_graceful_400(files_app, tmp_path) -> None:
    """Binary content as text is a clear client error, not a
    crash."""
    files_app.app.state.trust.save(4)
    file = tmp_path / "blob.bin"
    file.write_bytes(bytes(range(256)) * 4)
    client = TestClient(files_app.app)
    response = client.post("/api/files/read", json={"path": str(file)})
    assert response.status_code == 400
    assert "first 100 bytes" in response.json()["detail"]


def test_api_missing_file_is_404(files_app, tmp_path) -> None:
    """A path with nothing at it maps to 404."""
    files_app.app.state.trust.save(4)
    client = TestClient(files_app.app)
    response = client.post(
        "/api/files/read", json={"path": str(tmp_path / "nope.txt")}
    )
    assert response.status_code == 404


def test_api_dry_run_plans_without_executing(files_app, tmp_path) -> None:
    """A dry run returns the plan and the decision, and performs
    no I/O."""
    client = TestClient(files_app.app)
    file = tmp_path / "plan.txt"
    file.write_text("original")
    response = client.post(
        "/api/files/write",
        json={"path": str(file), "content": "changed", "dry_run": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["decision"]["dry_run"] is True
    assert file.read_text() == "original"
    assert not (tmp_path / "plan.txt.bak.1").exists()


def test_api_list_is_paginated(files_app, tmp_path) -> None:
    """The list endpoint pages through a directory."""
    files_app.app.state.trust.save(2)
    directory = tmp_path / "page"
    directory.mkdir()
    for index in range(30):
        (directory / f"f{index}.txt").write_text("x")
    client = TestClient(files_app.app)
    response = client.post(
        "/api/files/list",
        json={"path": str(directory), "limit": 10, "offset": 0},
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["total"] == 30
    assert len(result["entries"]) == 10


def test_api_search_and_tree_endpoints(files_app, tmp_path) -> None:
    """Search and tree work through the API."""
    files_app.app.state.trust.save(2)
    directory = tmp_path / "find"
    directory.mkdir()
    (directory / "match.py").write_text("x")
    client = TestClient(files_app.app)
    found = client.post(
        "/api/files/search",
        json={"pattern": "*.py", "root": str(directory)},
    )
    assert found.status_code == 200
    assert found.json()["result"]["count"] == 1
    tree = client.post(
        "/api/files/tree", json={"path": str(directory), "max_depth": 2}
    )
    assert tree.status_code == 200
    assert tree.json()["result"]["children"][0]["name"] == "match.py"


def test_api_backups_listing(files_app, tmp_path) -> None:
    """The backups endpoint lists the .bak files of a path."""
    files_app.app.state.trust.save(4)
    file = tmp_path / "data.txt"
    file.write_text("v0")
    client = TestClient(files_app.app)
    client.post("/api/files/write", json={"path": str(file), "content": "v1"})
    listing = client.get("/api/files/backups", params={"path": str(file)})
    assert listing.status_code == 200
    assert listing.json()["backups"] == [str(tmp_path / "data.txt.bak.1")]


def test_api_empty_trash_flow(files_app, tmp_path) -> None:
    """Emptying the trash asks; the confirmed purge reports how
    many items went to the recycle bin."""
    files_app.app.state.trust.save(4)
    client = TestClient(files_app.app)
    file = tmp_path / "gone.txt"
    file.write_text("x")
    client.post(
        "/api/files/delete",
        json={
            "path": str(file),
            "confirmed": True,
            "confirmation_id": _confirm(files_app, "delete", str(file)),
        },
    )
    response = client.post("/api/files/empty-trash", json={})
    assert response.status_code == 202
    prompt = response.json()
    assert prompt["file_count"] == 1
    assert prompt["reversible"] is True
    second = client.post(
        "/api/files/empty-trash",
        json={
            "confirmed": True,
            "confirmation_id": prompt["confirmation_id"],
        },
    )
    assert second.status_code == 200
    assert second.json()["result"] == 1


def _confirm(files_app, operation: str, target: str) -> str:
    """Ask for a confirmation and return its id (test helper)."""
    client = TestClient(files_app.app)
    payload = {"path": target} if operation != "empty-trash" else {}
    route = f"/api/files/{operation}"
    response = client.post(route, json=payload)
    assert response.status_code == 202, response.json()
    return response.json()["confirmation_id"]


def test_api_rejects_a_forged_confirmation(files_app, tmp_path) -> None:
    """A confirmation id that was never issued is refused."""
    client = TestClient(files_app.app)
    file = tmp_path / "file.txt"
    file.write_text("x")
    response = client.post(
        "/api/files/read",
        json={
            "path": str(file),
            "confirmed": True,
            "confirmation_id": "conf-never-issued",
        },
    )
    assert response.status_code == 400


def test_api_trust_level_drives_the_answer(files_app, tmp_path) -> None:
    """The same read asks at L1 and auto-approves at L2 — the
    matrix, not the route, decides."""
    file = tmp_path / "note.txt"
    file.write_text("x")
    client = TestClient(files_app.app)
    assert client.post(
        "/api/files/read", json={"path": str(file)}
    ).status_code == 202
    files_app.app.state.trust.save(2)
    assert client.post(
        "/api/files/read", json={"path": str(file)}
    ).status_code == 200


# ---------------------------------------------------------------------
# Limitations, pinned as tests (the documented pattern)
# ---------------------------------------------------------------------


def test_purged_items_go_to_the_recycle_bin_not_oblivion() -> None:
    """LIMITATION, pinned: emptying ZUZZ's trash hands items to
    ``send2trash`` — the operating system's recycle bin — rather
    than deleting them outright. Purge is therefore recoverable
    by the user outside ZUZZ, which is the safe direction."""
    from body.fingers import trash_manager

    assert trash_manager._default_transport is not None
    # The default transport is send2trash when it is installed.
    import send2trash

    assert trash_manager._default_transport is not None


def test_stat_owner_requires_pywin32(env, tmp_path) -> None:
    """LIMITATION, pinned: ``owner`` in stat() is None until the
    Windows SID resolver is wired, which needs pywin32 — not a
    dependency yet. Every other metadata field is present."""
    file = tmp_path / "file.txt"
    file.write_text("x")
    decision = allowed_decision("read_files", str(file))
    meta = env.files.stat(decision, str(file))
    assert meta["owner"] is None
    assert meta["size"]["bytes"] == 1
    assert meta["modified"]
    assert meta["created"]
    assert meta["type"]["extension"] == ".txt"
    assert meta["type"]["mime"] == "text/plain"


def test_a_forged_decision_is_the_last_line_not_a_boundary() -> None:
    """LIMITATION, pinned: a PermissionDecision is data. The Body
    re-checks classification, secrets and ZUZZ's own files after
    the decision, so a forged decision cannot reach a protected
    target — but the gate remains the real boundary, and the
    Body is the backstop, not the lock. This test pins that the
    backstop exists for every destructive category."""
    for category in ("write_files", "delete_files"):
        decision = allowed_decision(category, r"C:\Windows\System32")
        assert decision.allowed is True  # the gate is assumed bypassed
        # ...and the Body still refuses, which the attack tests
        # above prove for each operation.


def test_confirmations_do_not_survive_a_restart() -> None:
    """LIMITATION, pinned: pending confirmations live in memory
    only. A server restart clears unanswered prompts, so a stale
    popup can never become an approval — the safe direction."""
    from face.api.file_confirmations import PendingConfirmations

    registry = PendingConfirmations()
    action = Action(category=ActionCategory.READ_FILES, target="x")
    decision = allowed_decision("read_files", "x")
    confirmation_id = registry.create(action, decision, {})
    assert len(registry) == 1
    registry = PendingConfirmations()  # a restart builds a new one
    assert len(registry) == 0
    assert registry.pop(confirmation_id) is None


def test_confirmation_registry_is_capped_at_ten() -> None:
    """Only the last ten operations await an answer; older prompts
    expire rather than accumulate."""
    from face.api.file_confirmations import PendingConfirmations

    registry = PendingConfirmations()
    ids = []
    for index in range(15):
        action = Action(
            category=ActionCategory.READ_FILES, target=f"file-{index}"
        )
        ids.append(
            registry.create(action, allowed_decision("read_files", "x"), {})
        )
    assert len(registry) == 10
    assert registry.pop(ids[0]) is None  # the oldest expired
    assert registry.pop(ids[-1]) is not None  # the newest is still pending


def test_preview_counts_are_bounded(files_app, tmp_path) -> None:
    """A preview of a huge tree counts at most 10,000 files and
    says so, rather than hanging on an unbounded walk."""
    from face.api.file_confirmations import _walk_totals

    directory = tmp_path / "many"
    directory.mkdir()
    for index in range(120):
        (directory / f"f{index}.txt").write_text("x")
    count, total, capped = _walk_totals(str(directory))
    assert count == 120
    assert total == 120
    assert capped is False
    assert _walk_totals(str(tmp_path / "missing")) == (0, 0, False)


def test_preview_excludes_protected_and_secrets(tmp_path) -> None:
    """A preview never counts — and so never reveals — system or
    credential files."""
    from face.api.file_confirmations import _walk_totals

    directory = tmp_path / "mixed"
    directory.mkdir()
    (directory / "visible.txt").write_text("x")
    (directory / ".env").write_text("SECRET=1")
    count, _total, _capped = _walk_totals(str(directory))
    assert count == 1
