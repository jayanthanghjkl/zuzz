"""Hands — desktop control behind the gate.

Every public method takes a :class:`~brain.prefrontal_cortex.permission_decision.
PermissionDecision` as its first argument, with exactly three documented
exceptions that are system-level rather than agent actions:

* ``Hands.describe_layout`` and ``Hands.describe_policy`` — configuration the
  Desktop tab shows the user without asking, exactly as it shows the trust level
  without asking;
* ``Hands.release_held_keys`` — the cleanup path for an operation that has
  already failed. A stuck modifier key is a safety problem, and requiring a
  fresh decision to undo damage ZUZZ caused would be the wrong way round.

That list is closed, and
``test_public_methods_all_require_a_decision`` pins it: a new decision-free
method is a test failure, not a quiet widening of the surface that skips the
gate.

How the pieces fit:

* :mod:`body.hands.hands_guards` — the decision check every operation runs, and
  the dangerous-text check.
* :mod:`body.hands.input_base` — the checks the mouse and the keyboard share.
* :mod:`body.hands.hotkey_policy` — which key combinations may be synthesised.
* :mod:`body.hands.window_guards` — windows and processes ZUZZ will not drive.
* :mod:`body.hands.action_classifier` — operation to trust-category mapping, and
  target resolution.
* :mod:`body.hands.text_scanner` — what is dangerous to type, and what looks
  like a credential.
* :mod:`body.hands.actuator` — the only place PyAutoGUI is imported, behind the
  fail-safe.
* :mod:`body.hands.recorder` — the recording actuator the tests and the dry-run
  preview share.

The trust categories come from ``config/trust_levels.json``; the application
whitelist from ``config/apps_whitelist.json``; the hotkey lists from
``config/dangerous_hotkeys.json``. None of them is duplicated in code.
"""

from body.hands.actuator import Actuator, PyAutoGUIActuator
from body.hands.app_manager import AppManager
from body.hands.app_registry import resolve_app
from body.hands.clipboard import Clipboard
from body.hands.exceptions import (
    ActuatorError,
    DangerousHotkeyRefused,
    DangerousTextRefused,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    ElevatedWindowRefused,
    FailSafeTriggered,
    FullscreenRefused,
    HandsRefused,
    KillSwitchRefused,
    LoginDialogRefused,
    OffScreenRefused,
    ProtectedProcessRefused,
    ProtectedRefused,
    ReservedHotkeyRefused,
    TargetNotFoundRefused,
)
from body.hands.hands import Hands
from body.hands.keyboard import Keyboard
from body.hands.mouse import Mouse
from body.hands.recorder import RecordingActuator
from body.hands.screens import Screens

__all__ = [
    "Actuator",
    "ActuatorError",
    "AppManager",
    "Clipboard",
    "DangerousHotkeyRefused",
    "DangerousTextRefused",
    "DecisionRefused",
    "DecisionReplayRefused",
    "DryRunRefused",
    "ElevatedWindowRefused",
    "FailSafeTriggered",
    "FullscreenRefused",
    "Hands",
    "HandsRefused",
    "KillSwitchRefused",
    "Keyboard",
    "LoginDialogRefused",
    "Mouse",
    "OffScreenRefused",
    "ProtectedProcessRefused",
    "ProtectedRefused",
    "PyAutoGUIActuator",
    "RecordingActuator",
    "ReservedHotkeyRefused",
    "Screens",
    "TargetNotFoundRefused",
    "resolve_app",
]