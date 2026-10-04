"""Cerebrum — value coercion for tool parameters.

The conversions, and the refusals, that turn whatever a model wrote into the type
a tool declared — plus the bounds and enum checks that decide whether a value is
even usable.

This module owns :class:`ToolParamError` rather than importing it. It used to be
split the other way round, with the coercions in ``tools_coerce`` importing the
error out of ``tools_validate`` while ``tools_validate`` imported the coercions
back — a circular import that raised
``ImportError: cannot import name 'ToolParamError' from partially initialized
module``. Neither module could be imported first, so
:mod:`brain.cerebrum.action_parser` was dead code: importing it failed, which
meant no action block could ever be parsed and the whole agent loop was inert.
The error type is the thing everything else refers to, so it belongs at the
bottom of the dependency chain, here.

The refusals matter more than the coercions:

* a **boolean is never a number.** ``True`` arriving where a coordinate belongs
  is a model filling in a slot it did not understand, and coercing it to ``1``
  would put the pointer at the far left of the screen and click it.
* a **fractional coordinate is refused.** ``100.5`` is not somewhere; there is
  no pixel to click.
* a **number is never a string field.** ``42`` where a path belongs becomes
  ``"42"``, which is a real file name — a model guessing at a path is exactly
  the case that must not be allowed to invent one.
"""

from __future__ import annotations

from typing import Any, Dict, List

__all__ = [
    "MAX_CONTENT_CHARS",
    "MAX_NAME_CHARS",
    "ToolParamError",
    "as_number",
    "as_string",
    "as_string_list",
    "check_bounds",
    "check_enum",
    "coerce_value",
]


class ToolParamError(ValueError):
    """A tool call's parameters do not match the tool's schema."""


#: Longest file content accepted in one write. A model that wants to write more
#: should write a file and then append, and an unbounded string in a chat reply
#: is a denial-of-service vector dressed up as a convenience.
MAX_CONTENT_CHARS = 200_000

#: Longest path, application name, text or key sequence accepted.
MAX_NAME_CHARS = 512


def as_string(where: str, value: Any) -> str:
    """Coerce a value to a string parameter.

    Args:
        where: ``tool.parameter``, used in the error message.
        value: The value the model supplied.

    Returns:
        The string. Numbers are stringified, because a model writing ``42`` for
        a port or a process id meant the digits.

    Raises:
        ToolParamError: For anything else, including ``None`` and booleans.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise ToolParamError(f"{where} must be a string, got {type(value).__name__}")


def as_number(where: str, value: Any, kind: str) -> Any:
    """Coerce a value to an integer or a float parameter.

    Args:
        where: ``tool.parameter``, used in the error message.
        value: The value the model supplied.
        kind: ``integer`` or ``number``.

    Returns:
        An int for ``integer``, a float for ``number``.

    Raises:
        ToolParamError: If the value is a boolean, an unparseable string, or a
            float with a fractional part where a whole number was declared.
    """
    label = "a whole number" if kind == "integer" else "a number"
    if isinstance(value, bool):
        raise ToolParamError(f"{where} must be {label}, got a boolean")
    if isinstance(value, (int, float)):
        return _narrow(where, value, kind, label)
    if isinstance(value, str):
        try:
            return _narrow(where, float(value.strip()), kind, label)
        except ValueError as exc:
            raise ToolParamError(f"{where} must be {label}, got {value!r}") from exc
    raise ToolParamError(f"{where} must be {label}, got {type(value).__name__}")


def _narrow(where: str, value: Any, kind: str, label: str) -> Any:
    """Convert a float to an int when the schema asked for one.

    Args:
        where: ``tool.parameter``, used in the error message.
        value: The numeric value.
        kind: ``integer`` or ``number``.
        label: Human phrase for the expected type.

    Returns:
        The converted value.

    Raises:
        ToolParamError: If an integer was required and the value has a fraction.
    """
    if kind != "integer":
        return float(value)
    if isinstance(value, int):
        return value
    if float(value).is_integer():
        return int(value)
    raise ToolParamError(f"{where} must be {label}, got {value}")


def as_string_list(where: str, value: Any) -> List[str]:
    """Coerce a value to a list of names, accepting a comma-separated string.

    Args:
        where: ``tool.parameter``, used in the error message.
        value: The value the model supplied.

    Returns:
        A list of non-empty strings, in the order given.

    Raises:
        ToolParamError: If the value is not a usable list, or is empty. An empty
            combination is refused rather than passed on, because the keyboard
            layer would treat it as a combination to press, which is a different
            act from pressing nothing.
    """
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items
    elif isinstance(value, str) and value.strip():
        items = [part.strip() for part in value.split(",") if part.strip()]
        if items:
            return items
    raise ToolParamError(f"{where} must be a list of key names, got {value!r}")


def coerce_value(name: str, key: str, value: Any, declared: Dict[str, Any]) -> Any:
    """Coerce one parameter to its declared type.

    Args:
        name: The tool name.
        key: The parameter name.
        value: The value the model supplied.
        declared: The parameter's schema.

    Returns:
        The coerced value.

    Raises:
        ToolParamError: If the value cannot be used as declared.
    """
    where = f"{name}.{key}"
    declared_type = str(declared.get("type", "string"))
    if declared_type == "array":
        return as_string_list(where, value)
    if declared_type in ("integer", "number"):
        return as_number(where, value, declared_type)
    if declared_type == "boolean":
        if isinstance(value, bool):
            return value
        raise ToolParamError(f"{where} must be true or false")
    return as_string(where, value)


def check_enum(name: str, key: str, declared: Dict[str, Any], value: Any) -> None:
    """Refuse a value outside a declared enum.

    Args:
        name: The tool name.
        key: The parameter name.
        declared: The parameter's schema.
        value: The validated value.

    Raises:
        ToolParamError: If the parameter is an enum and the value is not in it.
    """
    options = declared.get("enum")
    if options and value not in options:
        raise ToolParamError(
            f"{name}.{key} must be one of "
            f"{', '.join(str(option) for option in options)}"
        )


def check_bounds(name: str, params: Dict[str, Any]) -> None:
    """Refuse parameters too large to be what the model meant.

    Args:
        name: The tool name.
        params: The validated parameters.

    Raises:
        ToolParamError: If a string parameter is over its cap.
    """
    for key, value in params.items():
        if not isinstance(value, str):
            continue
        cap = MAX_CONTENT_CHARS if key == "content" else MAX_NAME_CHARS
        if len(value) > cap:
            raise ToolParamError(
                f"{name}.{key} is {len(value)} characters; the limit is {cap}"
            )