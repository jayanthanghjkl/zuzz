"""Cerebrum — the tool catalogue, as the model sees it.

The presentation layer over :mod:`brain.cerebrum.tools_schema`: the same
catalogue in three shapes, depending on who is reading it.

* :func:`tool_catalog` — the compact list the system prompt imitates. This is
  the one in use today, because a fenced JSON block works identically on Ollama,
  on the Corpus Callosum connectors and through LiteLLM.
* :func:`as_openai_tools` — the ``tools=[...]`` array an OpenAI-compatible
  provider expects, for a backend that ever speaks native function calling.
* :func:`describe` — name, description and parameter summary as plain data, for
  a status endpoint and for the tests.

Keeping presentation out of ``tools_schema`` means the schema file is a list of
tools and nothing else, which is what makes it reviewable: a new tool is one
entry, and there is no way for its description and its schema to disagree.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from brain.cerebrum.tools_schema import TOOL_SCHEMAS

__all__ = ["TOOL_NAMES", "as_openai_tools", "describe", "tool_catalog"]

#: Tool names in catalogue order. The order is the order the prompt lists them,
#: so it is grouped the way a person groups them: pointer, keyboard, clipboard,
#: applications, files, then the machine itself.
TOOL_NAMES: Tuple[str, ...] = tuple(
    str(schema["function"]["name"]) for schema in TOOL_SCHEMAS
)


def describe() -> List[Dict[str, str]]:
    """Return every tool as name/description/parameter-summary triples.

    Returns:
        One dict per tool, in catalogue order.
    """
    return [
        {
            "name": str(schema["function"]["name"]),
            "description": str(schema["function"]["description"]),
            "parameters": ", ".join(
                f"{key}: {value.get('type', 'any')}"
                for key, value in schema["function"]["parameters"]["properties"].items()
            ),
        }
        for schema in TOOL_SCHEMAS
    ]


def as_openai_tools() -> List[Dict[str, Any]]:
    """Return the catalogue in the ``tools=[...]`` shape OpenAI-compatible APIs take.

    Not used by any backend today. It exists so that adding native function
    calling later is a plumbing change in one backend rather than a second
    description of what a tool is, written in two places and free to drift.
    """
    return [dict(schema) for schema in TOOL_SCHEMAS]


def tool_catalog() -> str:
    """Render the catalogue as the prompt section the model imitates.

    Kept short on purpose. A 400-line schema block crowds out the identity and
    the safety rules, and a small local model follows a short worked example
    more reliably than it follows a specification.
    """
    lines = ["Available tools — call these by their exact `tool` name:"]
    for entry in describe():
        params = f" ({entry['parameters']})" if entry["parameters"] else ""
        lines.append(f"- {entry['name']}{params}: {entry['description']}")
    return "\n".join(lines)
