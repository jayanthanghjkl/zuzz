"""Mirror Neurons — learn by watching.

Observes what the user does on their own desktop, finds the sequences that
repeat, and offers to turn them into routines the user can run with a word.

The region is deliberately narrow. It watches, it records, it detects, and it
*suggests* — it never arms a routine and never runs one. Arming is a separate
act the user takes from the Agents page, and running goes through the Basal
Ganglia's ordinary plan-and-execute path, which re-checks every step against
the trust matrix at the moment it runs. A mirror-neuron suggestion is a
recommendation with evidence behind it, not a stored permission.

See :mod:`brain.mirror_neurons.mirror_neurons` for the orchestrator.
"""

from brain.mirror_neurons.mirror_neurons import (
    MirrorNeurons,
    ObservationSession,
    Pattern,
)
from brain.mirror_neurons.exceptions import (
    MirrorNeuronsError,
    ObservationAlreadyActive,
    ObservationNotActive,
    PatternNotFound,
    MacroCreationRefused,
    ObservationRefused,
)
from brain.mirror_neurons.observation_filter import (
    ObservationFilter,
    FilterRule,
    FilterVerdict,
    DEFAULT_FILTER,
    always_ignore,
    never_ignore,
)
from brain.mirror_neurons.behavior_recorder import BehaviorRecorder, record
from brain.mirror_neurons.workflow_learner import WorkflowLearner, DEFAULT_MIN_REPETITIONS, learn
from brain.mirror_neurons.pattern_cloner import Pattern as PatternClass, Macro, RoutineStep, clone_pattern
from brain.mirror_neurons.macro_generator import generate_macro, MAX_MACRO_STEPS

__all__ = [
    "MirrorNeurons",
    "ObservationSession",
    "Pattern",
    "MirrorNeuronsError",
    "ObservationAlreadyActive",
    "ObservationNotActive",
    "PatternNotFound",
    "MacroCreationRefused",
    "ObservationRefused",
    "ObservationFilter",
    "FilterRule",
    "FilterVerdict",
    "DEFAULT_FILTER",
    "always_ignore",
    "never_ignore",
    "BehaviorRecorder",
    "record",
    "WorkflowLearner",
    "DEFAULT_MIN_REPETITIONS",
    "learn",
    "Pattern",
    "Macro",
    "RoutineStep",
    "clone_pattern",
    "generate_macro",
    "MAX_MACRO_STEPS",
]