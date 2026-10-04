"""Cerebrum — tool schemas.

The contract ZUZZ's own hand is described in. Every tool here corresponds to a
method that already exists in :mod:`body.hands` or :mod:`body.fingers`; this
module says *how to ask*, never *whether to allow*. Deciding is the Prefrontal
Cortex's job, and a schema that could allow something would invert the design.

Validation lives in :mod:`brain.cerebrum.tools_validate`, and the ways the
catalogue is presented - to the prompt, to a provider, to a status endpoint -
in :mod:`brain.cerebrum.tool_catalogue`. What is left here is the list itself,
which is what makes it reviewable: a new tool is one entry, and there is no way
for its description and its schema to disagree.

Nothing here imports the Body. :mod:`body.tools.registry` binds each name to an
implementation, and a name in this file with no implementation is a test failure
rather than a tool that silently does nothing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from brain.cerebrum.schema_dsl import array_of_strings, enum, integer, string, tool

__all__ = ["TOOL_SCHEMAS", "schema_for"]

#: Every tool ZUZZ can be asked to run, in catalogue order. The order is the
#: order the prompt lists them, so it is grouped the way a person would group
#: them: pointer, keyboard, clipboard, applications, files, then the machine.
TOOL_SCHEMAS: Tuple[Dict[str, Any], ...] = (
    tool(
        "mouse_click",
        "Click a point on the screen.",
        {
            "x": integer("Horizontal coordinate in pixels"),
            "y": integer("Vertical coordinate in pixels"),
            "button": enum("Which mouse button", "left", "right", "middle"),
        },
        ("x", "y"),
    ),
    tool(
        "mouse_move",
        "Move the pointer to a point without clicking.",
        {"x": integer("Horizontal coordinate"), "y": integer("Vertical coordinate")},
        ("x", "y"),
    ),
    tool(
        "mouse_scroll",
        "Scroll the mouse wheel where the pointer currently is.",
        {
            "amount": integer(
                "Wheel notches. Positive scrolls up, negative scrolls down, "
                "e.g. -3 for three notches down"
            )
        },
        ("amount",),
    ),
    tool(
        "keyboard_type",
        "Type a string into whatever window has focus.",
        {"text": string("The literal text to type")},
        ("text",),
    ),
    tool(
        "keyboard_hotkey",
        "Press a key combination together, e.g. ctrl+s or alt+tab.",
        {"keys": array_of_strings("Key names, e.g. [\"ctrl\", \"s\"]")},
        ("keys",),
    ),
    tool(
        "clipboard_read",
        "Read the text currently on the clipboard.",
        {},
    ),
    tool(
        "clipboard_write",
        "Replace the clipboard contents with a string.",
        {"text": string("The text to place on the clipboard")},
        ("text",),
    ),
    tool(
        "open_app",
        "Launch a desktop application by name, e.g. chrome, notepad, spotify.",
        {"app_name": string("Whitelist alias, exact title or process name")},
        ("app_name",),
    ),
    tool(
        "close_app",
        "Ask a running application to close, by process name or window title.",
        {"process_name": string("Process name, e.g. notepad.exe, or window title")},
        ("process_name",),
    ),
    tool(
        "file_read",
        "Read a text file and return its contents.",
        {"path": string("Absolute path, or a path relative to the project folder")},
        ("path",),
    ),
    tool(
        "file_write",
        "Write text to a file, creating it and any missing parent folders.",
        {
            "path": string("Absolute path, or a path relative to the project folder"),
            "content": string("The full text to write"),
        },
        ("path", "content"),
    ),
    tool(
        "file_delete",
        "Delete a file or folder. It goes to the recycle bin, so it is reversible.",
        {"path": string("Absolute path, or a path relative to the project folder")},
        ("path",),
    ),
    tool(
        "file_list",
        "List the contents of a folder.",
        {
            "path": string(
                "Absolute path, or a path relative to the project folder. "
                'Use "." for the project folder itself'
            )
        },
        ("path",),
    ),
    tool(
        "file_search",
        "Find files and folders by name under a root folder.",
        {
            "pattern": string("Name fragment to look for, e.g. *.py or report"),
            "root": string(
                "Where to start looking. Absolute, or relative to the project folder"
            ),
        },
        ("pattern", "root"),
    ),
    tool(
        "get_system_resources",
        "Read this machine's live RAM, VRAM, CPU, GPU and disk usage.",
        {},
    ),
)

_BY_NAME: Dict[str, Dict[str, Any]] = {
    str(schema["function"]["name"]): schema for schema in TOOL_SCHEMAS
}


def schema_for(name: str) -> Optional[Dict[str, Any]]:
    """Return one tool's schema, or None when the name is not a tool.

    Args:
        name: The tool name the model used.

    Returns:
        The schema, or None. Callers must treat None as "no such tool" and
        refuse; there is deliberately no default schema to fall back on.
    """
    return _BY_NAME.get(str(name))
