"""Cerebrum — tool parameter validation.

A tool call arrives as text a language model wrote. Before any of it can reach
the Body it is checked against the tool's schema in
:mod:`brain.cerebrum.tools_schema`, here.

The stance is **refuse rather than guess**. The coercions that are allowed —
``{"x": 100.0}`` means ``100``, ``"ctrl,s"`` means ``["ctrl", "s"]``, and ``42``
means ``"42"`` — live in :mod:`brain.cerebrum.tools_coercion` with the reasoning
for each. Everything else is refused, because a wrong guess about a delete's
path is not a bug report, it is a deleted file.

An **unknown parameter is an error**, not something to drop. A model that asked
for ``recursive`` on a delete and was quietly given a plain delete would then
believe it had removed a tree of files when it had removed one. Silently
ignoring a parameter leaves the model confident about a state of the world that
does not exist.

This module is :func:`validate` and nothing else — the schema walk. It was
previously the whole check, including the per-value coercion, the enum test and
the size bounds, at 209 lines; the three value-level checks moved to
``tools_coercion`` so this file reads as the sequence it actually is: resolve the
schema, reject the undeclared, reject the missing, coerce, fill defaults, check
the ranges.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.cerebrum.tools_coercion import (
    MAX_CONTENT_CHARS,
    MAX_NAME_CHARS,
    ToolParamError,
    check_bounds,
    check_enum,
    coerce_value,
)
from brain.cerebrum.tools_schema import schema_for

__all__ = ["MAX_CONTENT_CHARS", "MAX_NAME_CHARS", "ToolParamError", "validate"]


def validate(name: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Check one tool call's parameters against its schema.

    Args:
        name: The tool the model asked for.
        params: The parameters it supplied, or None for a no-argument tool.

    Returns:
        A new dict holding only declared parameters, coerced to their declared
        types, with schema defaults filled in.

    Raises:
        ToolParamError: For an unknown tool, a missing or undeclared parameter,
            a value of the wrong type, a value outside an enum, or an oversized
            string.
    """
    schema = schema_for(name)
    if schema is None:
        raise ToolParamError(f"{name!r} is not a tool ZUZZ has")
    parameters = schema["function"]["parameters"]
    declared: Dict[str, Any] = dict(parameters["properties"])
    supplied = dict(params or {})

    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        raise ToolParamError(
            f"{name} has no parameter(s): {', '.join(unknown)}. "
            f"It takes: {', '.join(declared) or 'nothing'}"
        )
    missing = [key for key in parameters["required"] if key not in supplied]
    if missing:
        raise ToolParamError(
            f"{name} is missing required parameter(s): {', '.join(missing)}"
        )

    validated = {
        key: coerce_value(name, key, value, declared[key])
        for key, value in supplied.items()
    }
    for key, definition in declared.items():
        if key not in validated and "default" in definition:
            validated[key] = definition["default"]
        if key in validated:
            check_enum(name, key, definition, validated[key])
    check_bounds(name, validated)
    return validated