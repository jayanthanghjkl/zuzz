"""Tools — the registry of specialised capabilities available to the brain.

This package is the bridge between what a language model *says* it wants and what
the Body actually does. A model emits a JSON action block; the Cerebrum parses
it into a :class:`~brain.cerebrum.action_parser.ToolCall`; this package resolves
that call into a concrete target and a Body method; and
:mod:`face.api.chat_actions` puts it through the Amygdala and the Prefrontal
Cortex before anything runs.

**Nothing in this package decides.** There is no gate here, no trust level, no
matrix. A tool can say what it would act on and refuse a target that does not
exist, and that is the whole of its authority. Deciding is the Prefrontal
Cortex's, and the ordering matters: :meth:`ToolSpec.resolve` runs *before* the
gate, so the gate judges the executable rather than the alias, and
:meth:`ToolSpec.execute` runs *after* it, so no Body method is ever reached
without a decision made about that exact target.

See :mod:`body.tools.tool_spec` for the two halves, :mod:`body.tools.registry`
for the name-to-implementation binding, and
:mod:`face.api.chat_actions` for the safety pipeline that drives them.
"""

from body.tools.registry import ToolRegistry, catalogue_mismatches, get_registry
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget

__all__ = [
    "ToolContext",
    "ToolRefused",
    "ToolRegistry",
    "ToolSpec",
    "ToolTarget",
    "catalogue_mismatches",
    "get_registry",
]
