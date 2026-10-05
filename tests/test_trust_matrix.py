"""Trust matrix tests.

The matrix is the single place where "what may ZUZZ do without asking" is
defined. These tests restate the approved permissions independently of
``config/trust_levels.json``, so widening access in the config without changing
the spec fails here rather than passing unnoticed.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_matrix import ASK, AUTO, BLOCKED
from brain.prefrontal_cortex.trust_matrix import TrustMatrix

ALL_LEVELS = (1, 2, 3, 4, 5)


def category_of(name: str) -> Any:
    """Return an action category for ``name``, preferring the enum member.

    ``manage_models`` is a config-only category: the matrix row is what makes it
    real, and the safety core is locked against editing the enum. ``Action`` does
    not enforce its annotation and the matrix looks categories up as strings, so
    the raw value behaves identically — the same way
    ``test_unknown_category_is_not_auto_approved`` already passes a bare string.
    """
    try:
        return ActionCategory(name)
    except ValueError:
        return name


#: The approved matrix, per the decisions of 2026-10-02, as widened by Step 5.
#: Index 0 is level 1.
#:
#: ``close_apps`` and ``fullscreen_control`` were added for the Hands layer.
#: Both ask at every level, and both are here rather than in the config alone so
#: that adding either one to the config is a test failure until this file says
#: so too. Closing an application can lose unsaved work, and input into a game
#: or a presentation is not the same act as input into a text field; neither
#: deserves a level at which it becomes silent.
#:
#: ``manage_models`` was added for the Hypothalamus in Step 6. It auto-approves
#: from level 3 for the same reason ``mouse_keyboard`` does: swapping the model
#: the user is already talking to is routine operator work once the trust level
#: says the operator is in charge. It asks at levels 1 and 2, where evicting a
#: model someone chose by hand is not the assistant's call.
#:
#: ``visual_capture`` was added in Session 11 for the Occipital Lobe. It asks at
#: levels 1 and 2 and auto-approves from 3, deliberately borrowing
#: ``mouse_keyboard``'s shape rather than ``read_files``'s: looking at the screen is
#: not reading a file the user pointed at, because the user never chose what was on
#: it and a screenshot can hold a credential that no file listing would have shown.
#: It is also not as dangerous as closing an application or reaching an
#: ``access_credentials`` path, which is why it does not ask at every level — a
#: screenshot is one of the main things an operator asks for, and a prompt on every
#: one would train people to click prompts through, which is the failure mode that
#: makes prompts worse than none.
APPROVED_MATRIX: Dict[str, tuple] = {
    "chat": (AUTO, AUTO, AUTO, AUTO, AUTO),
    "read_files": (ASK, AUTO, AUTO, AUTO, AUTO),
    "open_apps": (ASK, AUTO, AUTO, AUTO, AUTO),
    "close_apps": (ASK, ASK, ASK, ASK, ASK),
    "fullscreen_control": (ASK, ASK, ASK, ASK, ASK),
    "mouse_keyboard": (ASK, ASK, AUTO, AUTO, AUTO),
    "run_safe_commands": (ASK, ASK, AUTO, AUTO, AUTO),
    "write_files": (ASK, AUTO, AUTO, AUTO, AUTO),
    "browse": (ASK, ASK, ASK, AUTO, AUTO),
    "run_python": (ASK, ASK, ASK, ASK, AUTO),
    "modify_plugins": (ASK, ASK, ASK, ASK, AUTO),
    "delete_files": (ASK, ASK, ASK, ASK, ASK),
    "install_software": (ASK, ASK, ASK, ASK, ASK),
    "send_messages": (ASK, ASK, ASK, ASK, ASK),
    "run_admin": (ASK, ASK, ASK, ASK, ASK),
    "access_credentials": (ASK, ASK, ASK, ASK, ASK),
    "delete_os_files": (BLOCKED, BLOCKED, BLOCKED, BLOCKED, BLOCKED),
    "share_data": (BLOCKED, BLOCKED, BLOCKED, BLOCKED, BLOCKED),
    "disable_safety": (BLOCKED, BLOCKED, BLOCKED, BLOCKED, BLOCKED),
    "manage_models": (ASK, ASK, AUTO, AUTO, AUTO),
    "visual_capture": (ASK, ASK, AUTO, AUTO, AUTO),
    # The browser's three. Each is stricter than the operation it resembles, and the
    # direction of that is the point: reaching the web is narrower than opening an
    # app, acting *on* a page is no wider than controlling the desktop, and fetching
    # a file stays an ask at every level — including 5, which auto-approves every
    # other kind of file work. A download is the one file operation a fully trusted
    # ZUZZ must still ask about, because the file arrives from somewhere else.
    "browse_internet": (ASK, ASK, ASK, ASK, AUTO),
    "web_automation": (ASK, ASK, AUTO, AUTO, AUTO),
    "download_files": (ASK, ASK, ASK, ASK, ASK),
    # The Parietal Lobe's row, and the only one in this file that auto-approves at
    # trust level 1. Stated here as a spec cell rather than left to the config so that
    # "auto at every level" is a decision somebody wrote down and a test checks,
    # rather than a default nobody looked at. The argument is in
    # config/trust_levels.json: a window title is not a file, it cannot hold a
    # rendered bank portal the way a screenshot can, and the inference it feeds is
    # reduced to one word on this machine before the model sees anything. Over-prompting
    # here would train users to click the approval without reading it, which is the
    # cost the row is deliberately accepting.
    "system_awareness": (AUTO, AUTO, AUTO, AUTO, AUTO),
    # Phase 6. Two rows with opposite shapes, and the shape is the point.
    #
    # ``memory_access`` is auto everywhere, which is a real departure from level
    # 1's "ask for everything" and the second row in this file to do it. Stated as
    # a spec cell rather than left to the config so that "auto at every level" is a
    # decision somebody wrote down and a test checks. Memory is local, and the
    # Memory page makes every stored item readable and erasable by hand, so the
    # permission the row withholds is available on demand instead of as a dialog.
    # A prompt on every "as you mentioned" is how people learn to click prompts
    # through, which is the cost this row deliberately accepts.
    #
    # ``routine_management`` asks at 1 and 2 and borrows mouse_keyboard, because a
    # routine is a stored bundle of desktop acts and the user approved those acts
    # individually. It is not auto at the low levels even though reading is
    # harmless: the category also covers writing, and the matrix has no
    # per-operation axis, so a row loose for reading has to be loose for writing.
    "memory_access": (AUTO, AUTO, AUTO, AUTO, AUTO),
    "routine_management": (ASK, ASK, AUTO, AUTO, AUTO),
    # ``personality_access`` is the third auto-everywhere row, and the cheapest of
    # the three: it reaches no file, no window and no other machine. Reading the
    # register and shifting one editable knob are both things ZUZZ does *about
    # its own replies*, and a permission dialog there would be a prompt the user
    # has to read in order to find out whether ZUZZ was going to sound friendlier.
    # The write half is bounded by the region instead — only keys the Cerebellum
    # lists as editable can be set, which keeps the owner's name out of reach.
    "personality_access": (AUTO, AUTO, AUTO, AUTO, AUTO),
    # Phase 4, body/mouth/kilo. Stated here rather than left to the config, because
    # these five are the first rows whose strongest capability is unattended at level
    # 4 and the argument for each is the thing most worth pinning.
    #
    # generate_code and modify_project borrow mouse_keyboard's shape. modify_project is
    # stricter than write_files, which auto-approves from 2 — the gap is deliberate and
    # is the row's whole reason for existing: a bulk rewrite of a project directory is
    # not the act the user approved when they trusted ZUZZ with reading a file.
    #
    # execute_code is identical to run_python on purpose. A test file the agent wrote a
    # moment ago is not safer to run than one the user wrote.
    #
    # git_operations is asked through level 4 because it can make commits unreachable.
    # Read-only git is not in this row; it is read_files, mapped in kilo_git.
    #
    # install_packages asks at every level including 5, and that is immutable rule R7
    # rather than a preference: a wheel runs its author's build hooks, and an agent loop
    # can hallucinate a package name. This is the fifth ask-everywhere row alongside
    # install_software, download_files, delete_files, run_admin and access_credentials.
    "generate_code": (ASK, ASK, AUTO, AUTO, AUTO),
    "execute_code": (ASK, ASK, ASK, ASK, AUTO),
    "modify_project": (ASK, ASK, AUTO, AUTO, AUTO),
    "git_operations": (ASK, ASK, ASK, ASK, AUTO),
    "install_packages": (ASK, ASK, ASK, ASK, ASK),
}


@pytest.fixture
def gate() -> PermissionGate:
    """Return a gate reading the real permission matrix."""
    return PermissionGate()


@pytest.mark.parametrize("category,expected", sorted(APPROVED_MATRIX.items()))
def test_trust_matrix_matches_the_approved_spec(
    gate: PermissionGate, category: str, expected: tuple
) -> None:
    """Every cell of the approved matrix holds."""
    action = Action(category=category_of(category))
    for index, level in enumerate(ALL_LEVELS):
        decision = gate.decide(action, level)
        assert decision.decision == expected[index], (
            f"{category} at level {level}: expected {expected[index]}, got {decision.decision}"
        )


def test_matrix_file_and_test_agree(gate: PermissionGate) -> None:
    """The config file and the approved spec describe the same permissions."""
    assert set(gate.matrix.actions) == set(APPROVED_MATRIX)
    for category, expected in APPROVED_MATRIX.items():
        row = gate.row_for(category_of(category))
        assert tuple(row[str(level)] for level in ALL_LEVELS) == expected


def test_unknown_category_is_not_auto_approved(gate: PermissionGate) -> None:
    """A category missing from the matrix asks rather than assuming permission."""
    unlisted = Action(category="launch_missiles", target="everything")  # type: ignore[arg-type]
    assert gate.matrix_decision(unlisted.category, 5) == ASK
    assert gate.decide(unlisted, 5).requires_confirmation


def test_every_category_has_a_matrix_row() -> None:
    """Every declared category is configured, so none silently asks forever."""
    matrix = TrustMatrix()
    missing = [str(c) for c in ActionCategory if not matrix.is_known(c)]
    assert missing == []


def test_permissions_never_widen_with_level(gate: PermissionGate) -> None:
    """Raising the trust level may only ever add permission, never remove it."""
    order = {BLOCKED: 0, ASK: 1, AUTO: 2}
    for category in ActionCategory:
        previous = -1
        for level in ALL_LEVELS:
            current = order[gate.matrix_decision(category, level)]
            assert current >= previous, f"{category} narrowed at level {level}"
            previous = current


def test_unknown_levels_are_clamped(gate: PermissionGate) -> None:
    """A bogus level resolves inside 1-5, never outside the supported range."""
    assert gate.decide(Action(category=ActionCategory.RUN_PYTHON), 0).trust_level == 1
    assert gate.decide(Action(category=ActionCategory.RUN_PYTHON), 99).trust_level == 5


def test_policy_defaults_are_the_approved_ones() -> None:
    """The temp-deletion threshold and write policy match the decisions."""
    matrix = TrustMatrix()
    assert matrix.delete_temp_auto_from_level() == 3
    assert matrix.blocks_protected_writes() is True
    assert matrix.default_level() == 1