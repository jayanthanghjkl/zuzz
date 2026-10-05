"""Basal Ganglia — routine refusals.

The region's errors, so the API can map each onto the right HTTP status instead
of collapsing every failure into a 500. Four distinctions matter to a caller:

* a **refusal** — an unknown routine, an unusable name, a trigger that names
  nothing — is a bad request and a 400;
* :class:`RoutineNotPermitted` is the gate's answer and is a 403. It is separate
  from a bad request because the request was perfectly well formed: ZUZZ was not
  allowed to run it, which at a higher trust level is the same request succeeding;
* :class:`StepRefused` is a *precheck* on one step, raised before anything runs.
  It is the case worth having a class for: without one, a five-step routine could
  be refused halfway through, having already opened three applications;
* :class:`TriggerRefused` is about when a routine fires, and never about whether
  it may. A trigger that does not fire is not a failure at all.

Everything is a :class:`RoutineRefused`, following the naming convention used
across the brain.
"""

from __future__ import annotations


class RoutineRefused(Exception):
    """Base class for every Basal Ganglia refusal."""


class UnknownRoutine(RoutineRefused):
    """No routine by that name is stored.

    Distinct from :class:`RoutineDisabled`, which exists but is switched off: the
    user asking to run a routine they deleted needs to be told it is gone, not
    that it is unavailable.
    """


class RoutineDisabled(RoutineRefused):
    """The routine exists but is switched off.

    An answer rather than a fault. ``RoutineDisabled`` carries the stored routine
    so a caller can offer to switch it back on instead of making the user retype
    a command.
    """

    def __init__(self, message: str, routine: object = None) -> None:
        """Record the message and the routine behind it.

        Args:
            message: Human-readable explanation.
            routine: The stored :class:`~brain.basal_ganglia.routine_builder.Routine`.
        """
        super().__init__(message)
        self.routine = routine


class RoutineNameInvalid(RoutineRefused):
    """A routine name could not be used.

    Names must be short, lowercase and identifier-shaped, because a routine name
    reaches three places a name has to survive: a CLI argument, a URL path
    segment, and a key in ``config/routines.json``. A name with a slash or a
    newline in it is a request to write a key that will be painful to remove by
    hand later, so it is refused at the door rather than sanitised silently —
    silently rewriting a name would leave the user unable to find what they
    created.
    """


class RoutineNotPermitted(RoutineRefused):
    """The trust matrix would not allow this routine to run.

    Attributes:
        trust_level: The level in force when the decision was made.
        refused: Per-step detail naming which step asked for too much, so the
            refusal can be explained rather than merely asserted.
    """

    def __init__(self, message: str, trust_level: int = 1, refused: list = None) -> None:
        """Record the message and the decision behind it.

        Args:
            message: Human-readable explanation.
            trust_level: The trust level the decision was made at.
            refused: One ``{"step": ..., "tool": ..., "reason": ...}`` entry per
                step that was not allowed.
        """
        super().__init__(message)
        self.trust_level = int(trust_level)
        self.refused = list(refused or [])


class StepRefused(RoutineRefused):
    """A routine step named something ZUZZ cannot act on.

    Raised before anything runs, and for the whole routine: a routine whose third
    step does not exist would otherwise open two applications and then stop,
    leaving the desktop half-configured with no user action to explain it.
    """


class TriggerRefused(RoutineRefused):
    """A trigger was not one of the kinds ZUZZ recognises.

    Never about permission. A trigger that names an application ZUZZ cannot see
    simply never fires; a trigger that says ``kind="sometimes"`` is a typo and is
    refused, because a silently-ignored trigger would leave the user believing a
    routine is armed when it is not.
    """