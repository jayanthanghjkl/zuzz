"""Body/Hands — refusals.

The Hands layer executes desktop actions that a :class:`~brain.prefrontal_cortex.
permission_decision.PermissionDecision` already allowed. When it refuses, it
raises one of these and the API maps the type onto an HTTP status.

The hierarchy matters twice over. Anything ending in
:class:`ProtectedRefused` is a 403, because it means the target is something ZUZZ
must never touch at any trust level. And everything ending in
:class:`KillSwitchRefused` is a 409, because while the stop is engaged the honest
answer is that nothing runs — that outranks any judgement about the action
itself.
"""

from __future__ import annotations


class HandsRefused(Exception):
    """Base class for every Hands-layer refusal."""


# -- the stop, and the decision -------------------------------------------


class KillSwitchRefused(HandsRefused):
    """The kill switch was engaged, or engaged after the decision."""


class DecisionRefused(HandsRefused):
    """The operation reached the Hands without permission to run.

    Covers a missing decision, one that is not allowed, and one issued for a
    different operation.
    """


class DecisionReplayRefused(DecisionRefused):
    """A decision was presented for a different operation or target."""


class DryRunRefused(HandsRefused):
    """A dry-run decision reached the Hands, which must not actuate."""


# -- targets ZUZZ must never touch ---------------------------------------


class ProtectedRefused(HandsRefused):
    """The target is something ZUZZ must never drive."""


class DangerousHotkeyRefused(ProtectedRefused):
    """The key combination is on the always-blocked list."""


class ReservedHotkeyRefused(ProtectedRefused):
    """The combination is ZUZZ's own emergency stop.

    Refused because the stop must always be something a human presses.
    An agent that can synthesise ``Ctrl+Shift+Q`` can stop itself, and a
    stop it can cause is not a stop.
    """


class DangerousTextRefused(ProtectedRefused):
    """The text to type carries a pattern that must not be typed."""


class OffScreenRefused(ProtectedRefused):
    """The coordinate is outside every attached display."""


class ProtectedProcessRefused(ProtectedRefused):
    """The process is a system or ZUZZ-owned process."""


class LoginDialogRefused(ProtectedRefused):
    """The window is a login or credential prompt.

    Windows will not let a normal process type into them anyway, so this is
    the Body saying no before the OS says no.
    """


class ElevatedWindowRefused(ProtectedRefused):
    """The window runs elevated and this process does not.

    Reported rather than worked around: ZUZZ never requests elevation for
    itself, so the answer is an explanation and not an attempt.
    """


class FullscreenRefused(ProtectedRefused):
    """A full-screen window has focus and no permission was given."""


# -- state the caller got wrong -------------------------------------------


class TargetNotFoundRefused(HandsRefused):
    """No window, application or process matched the description."""


class InvalidInputRefused(HandsRefused):
    """An argument was outside the range the operation accepts."""


class NotWhitelistedRefused(DecisionRefused):
    """The application is not on the auto-approve list.

    A subclass of the decision refusal because the answer is a permission
    question, not a broken input: the route maps it to 403 when no
    confirmation is possible and treats it as an ``ask`` otherwise.
    """


class ActuatorError(HandsRefused):
    """The desktop refused the action, or PyAutoGUI is unavailable."""


class FailSafeTriggered(ActuatorError):
    """The cursor reached a screen corner and PyAutoGUI aborted.

    This is the last of three independent stops: the gate is first, the
    hotkey is second, and this is third. It fires even when ZUZZ asked
    politely and was told yes.
    """