"""Cerebrum — schema construction helpers.

Four tiny builders, kept apart from the catalogue itself so the tool list in
:mod:`brain.cerebrum.tools_schema` reads as a list of tools rather than as a
wall of dictionary nesting. Nothing here knows what a tool does.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

__all__ = ["array_of_strings", "enum", "integer", "string", "tool"]


def string(description: str) -> Dict[str, Any]:
    """Return a string property schema.

    Args:
        description: What the parameter is, written for the model.

    Returns:
        The property schema.
    """
    return {"type": "string", "description": description}


def enum(description: str, *values: str) -> Dict[str, Any]:
    """Return a string property schema restricted to ``values``.

    Args:
        description: What the parameter is, written for the model.
        *values: The permitted values.

    Returns:
        The property schema.
    """
    return {"type": "string", "description": description, "enum": list(values)}


def integer(description: str) -> Dict[str, Any]:
    """Return an integer property schema.

    Args:
        description: What the parameter is, written for the model.

    Returns:
        The property schema.
    """
    return {"type": "integer", "description": description}


def array_of_strings(description: str) -> Dict[str, Any]:
    """Return an array-of-strings property schema.

    Args:
        description: What the parameter is, written for the model.

    Returns:
        The property schema.
    """
    return {"type": "array", "items": {"type": "string"}, "description": description}


def tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Wrap one tool's parameters in the JSON Schema envelope.

    ``additionalProperties`` is ``False`` on purpose. A model that invents a
    parameter is guessing, and silently ignoring the guess would let it believe
    it had set an option it never set — a ``recursive`` flag on a delete, or a
    ``force`` flag that does not exist. Refusing an unknown parameter is what
    makes the schema worth having.

    Args:
        name: The tool name.
        description: What the tool does, for the model to read.
        properties: The parameter schemas.
        required: Parameter names that must be present.

    Returns:
        The complete tool schema.
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }
