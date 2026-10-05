"""Basal Ganglia — habit detection and routine automation.

Turns what the user actually does into things ZUZZ can do again on request.

Six modules and one orchestrator:

* :mod:`brain.basal_ganglia.pattern_detector` — reads the activity log and finds
  sequences that keep repeating.
* :mod:`brain.basal_ganglia.routine_builder` — turns a pattern into a routine of
  existing tool calls, and holds the three shipped routines.
* :mod:`brain.basal_ganglia.context_triggers` — decides *when* a routine fires.
* :mod:`brain.basal_ganglia.routine_registry` — stores routines in
  ``config/routines.json``.
* :mod:`brain.basal_ganglia.exceptions` — the refusal hierarchy.
* :mod:`brain.basal_ganglia.basal_ganglia` — the orchestrator, and the place the
  permission gate is consulted.

**Two properties the region exists to hold.**

*A routine is a bundle of tool calls, not a private action path.* Every
:class:`~brain.basal_ganglia.routine_builder.RoutineStep` names a tool that
already exists in :mod:`brain.cerebrum.tools_schema`, and running a routine walks
the ordinary ``resolve`` → gate → ``execute`` pipeline. If routines could hold
their own action handlers they would be a way to touch the Body that no gated
tool call has, and the whole architecture exists to make sure there isn't one.

*A routine stores no permission.* The gate is asked at the moment the routine
runs, at the trust level in force then, for every step
(:meth:`~brain.basal_ganglia.basal_ganglia.BasalGanglia.plan`). A routine created
at level 5 and executed at level 1 does only what level 1 permits. This is why
:class:`~brain.basal_ganglia.routine_builder.Routine` has no decision field to
put an approval in — there is nowhere for one to be stored.

**The precheck is all-or-nothing.** Every step is resolved and decided before the
first one runs, and one refusal abandons the whole routine. Executing in order and
stopping at the first refusal would leave a five-step routine half-applied — two
applications open, no window focused, nothing the user asked for — so a refusal
costs nothing and a partial run is not reachable.
``config/trust_levels.json``'s ``routine_note`` has the reasoning behind the row.
"""

from brain.basal_ganglia.basal_ganglia import (
    ROUTINE_CATEGORY,
    BasalGanglia,
    PlannedStep,
    RoutinePlan,
    RoutineResult,
)
from brain.basal_ganglia.context_triggers import (
    APP,
    COMMAND,
    KIND_VALUES,
    MANUAL,
    TIME,
    Trigger,
    context_from,
    describe,
    due,
    parse,
)
from brain.basal_ganglia.exceptions import (
    RoutineDisabled,
    RoutineNameInvalid,
    RoutineNotPermitted,
    RoutineRefused,
    StepRefused,
    TriggerRefused,
    UnknownRoutine,
)
from brain.basal_ganglia.pattern_detector import (
    DEFAULT_MIN_REPETITIONS,
    ObservedAction,
    Pattern,
    detect_patterns,
)
from brain.basal_ganglia.routine_builder import (
    MAX_ROUTINE_NAME,
    MAX_STEPS,
    SEED_ROUTINES,
    Routine,
    RoutineStep,
    build_from_pattern,
    seed_routines,
    suggest_name,
    validate_name,
)
from brain.basal_ganglia.routine_registry import RoutineRegistry, routine_from_dict

__all__ = [
    "APP",
    "BasalGanglia",
    "COMMAND",
    "DEFAULT_MIN_REPETITIONS",
    "KIND_VALUES",
    "MANUAL",
    "MAX_ROUTINE_NAME",
    "MAX_STEPS",
    "ObservedAction",
    "Pattern",
    "PlannedStep",
    "ROUTINE_CATEGORY",
    "Routine",
    "RoutineDisabled",
    "RoutineNameInvalid",
    "RoutineNotPermitted",
    "RoutinePlan",
    "RoutineRefused",
    "RoutineRegistry",
    "RoutineResult",
    "RoutineStep",
    "SEED_ROUTINES",
    "TIME",
    "Trigger",
    "TriggerRefused",
    "UnknownRoutine",
    "build_from_pattern",
    "context_from",
    "describe",
    "detect_patterns",
    "due",
    "parse",
    "routine_from_dict",
    "seed_routines",
    "suggest_name",
    "validate_name",
]