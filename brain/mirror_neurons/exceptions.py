"""Mirror Neurons — exceptions.

Every failure here is a *refusal*, never a silent skip. The region watches the
user's desktop and the failure modes are all of the form "something the user
would want to know did not happen": an observation that cannot start because
one is already running, a pattern that was never detected, a macro that cannot
be built from what was seen.

None of these are permission decisions. The gate is consulted by the tools
that expose this region, and the region itself only refuses on the grounds
that the thing asked for does not exist or cannot be built.
"""

from __future__ import annotations


class MirrorNeuronsError(Exception):
    """Base class for every refusal from the Mirror Neurons region."""


class ObservationAlreadyActive(MirrorNeuronsError):
    """Raised when ``start_observation`` is called while one is running.

    Two observations at once would each record the other's acts into its own
    log, and the patterns that came out would be a mess of interleaved
    sequences nobody could interpret. Refusing is the cheap way to keep the
    data honest.
    """


class ObservationNotActive(MirrorNeuronsError):
    """Raised when ``stop_observation`` or ``get_patterns`` runs with none open."""


class PatternNotFound(MirrorNeuronsError):
    """Raised when a pattern id names something that was never detected."""


class MacroCreationRefused(MirrorNeuronsError):
    """Raised when a pattern cannot become a routine.

    The common cause is a pattern whose steps name tools ZUZZ does not have.
    That is checked here rather than left to the Basal Ganglia at save time,
    because the user is being asked to approve a macro and a macro that
    cannot run is a bad answer to that question.
    """


class ObservationRefused(MirrorNeuronsError):
    """Raised when an observation cannot start for a reason the caller should see.

    The message is carried to the user. The two cases that produce it are a
    privacy filter that has excluded everything the recorder would watch, and
    a recorder that was never attached — in both cases there is nothing to
    observe and saying so is the honest answer.
    """


__all__ = [
    "MirrorNeuronsError",
    "ObservationAlreadyActive",
    "ObservationNotActive",
    "PatternNotFound",
    "MacroCreationRefused",
    "ObservationRefused",
]