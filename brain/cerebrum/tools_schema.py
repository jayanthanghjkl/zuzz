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
#: them: pointer, keyboard, clipboard, applications, files, vision, browser, then the machine.
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
    # Vision tools (Session 2)
    tool(
        "vision_capture_screen",
        "Take a screenshot of a monitor or region.",
        {
            "monitor": integer("Zero-based monitor index"),
            "region": {
                "type": "object",
                "description": "Optional region as {x, y, width, height} in monitor-local pixels",
                "properties": {
                    "x": integer("Left"),
                    "y": integer("Top"),
                    "width": integer("Width"),
                    "height": integer("Height"),
                },
                "required": ["x", "y", "width", "height"],
            },
        },
    ),
    tool(
        "vision_describe_screen",
        "Describe what is on the screen in natural language.",
        {
            "monitor": integer("Zero-based monitor index"),
            "prompt": string("Optional focus question, e.g. 'what is this dialog asking'"),
            "region": {
                "type": "object",
                "description": "Optional region as {x, y, width, height} in monitor-local pixels",
                "properties": {
                    "x": integer("Left"),
                    "y": integer("Top"),
                    "width": integer("Width"),
                    "height": integer("Height"),
                },
                "required": ["x", "y", "width", "height"],
            },
        },
    ),
    tool(
        "vision_find_element",
        "Locate a UI element by description and return its coordinates.",
        {
            "description": string("What to look for, in the user's words"),
            "monitor": integer("Zero-based monitor index"),
        },
        ("description",),
    ),
    tool(
        "vision_read_text",
        "Extract text from a screen region using OCR.",
        {
            "region": {
                "type": "object",
                "description": "Region as {x, y, width, height} in monitor-local pixels",
                "properties": {
                    "x": integer("Left"),
                    "y": integer("Top"),
                    "width": integer("Width"),
                    "height": integer("Height"),
                },
                "required": ["x", "y", "width", "height"],
            },
            "source": enum("Source of the image", "current", "stored"),
        },
        ("region",),
    ),
    tool(
        "vision_verify_changed",
        "Compare two screenshots to verify a state change.",
        {
            "before": string("First capture identifier (path, bytes, or stored name)"),
            "after": string("Second capture identifier (path, bytes, or stored name)"),
        },
        ("before", "after"),
    ),
    # Browser tools (Session 3)
    tool(
        "browser_open",
        "Launch a browser session.",
        {
            "headless": enum("Run without a visible window", "false", "true"),
        },
    ),
    tool(
        "browser_navigate",
        "Go to a URL in an open browser session.",
        {
            "url": string("The URL to navigate to"),
            "session_id": string("Optional session ID; uses the most recent if omitted"),
        },
        ("url",),
    ),
    tool(
        "browser_click",
        "Click an element on the page by selector or visible text.",
        {
            "selector_or_text": string("CSS selector or visible text of the element to click"),
            "session_id": string("Optional session ID"),
        },
        ("selector_or_text",),
    ),
    tool(
        "browser_fill_form",
        "Fill form fields on the current page.",
        {
            "form_data": {
                "type": "object",
                "description": "Field identifiers mapped to values",
                "additionalProperties": {"type": "string"},
            },
            "session_id": string("Optional session ID"),
        },
        ("form_data",),
    ),
    tool(
        "browser_extract_text",
        "Extract text from a page element.",
        {
            "selector": string("CSS selector of the element to extract text from"),
            "session_id": string("Optional session ID"),
        },
        ("selector",),
    ),
    tool(
        "browser_extract_links",
        "Get all links on the current page.",
        {
            "session_id": string("Optional session ID"),
        },
    ),
    tool(
        "browser_search_web",
        "Perform a web search.",
        {
            "query": string("What to search for"),
            "engine": enum("Search engine", "google", "duckduckgo", "bing"),
            "session_id": string("Optional session ID"),
        },
        ("query",),
    ),
    tool(
        "browser_download",
        "Download a file through the Fingers classifier into quarantine.",
        {
            "url": string("The URL to download"),
            "session_id": string("Optional session ID"),
        },
        ("url",),
    ),
    tool(
        "browser_screenshot",
        "Take a screenshot of the current browser view.",
        {
            "session_id": string("Optional session ID"),
            "full_page": enum("Capture the full scrollable page", "false", "true"),
        },
    ),
    tool(
        "browser_list_tabs",
        "List all open tabs in a session.",
        {
            "session_id": string("Optional session ID"),
        },
    ),
    tool(
        "browser_switch_tab",
        "Switch the active tab.",
        {
            "tab_id": string("Tab index or ID to switch to"),
            "session_id": string("Optional session ID"),
        },
        ("tab_id",),
    ),
    tool(
        "browser_close_tab",
        "Close a specific tab.",
        {
            "tab_id": string("Tab index or ID to close"),
            "session_id": string("Optional session ID"),
        },
        ("tab_id",),
    ),
    tool(
        "browser_close",
        "Close a browser session.",
        {
            "session_id": string("Optional session ID"),
        },
    ),
    tool(
        "browser_scroll",
        "Scroll the page up or down.",
        {
            "amount": integer("Pixels to scroll; negative goes up"),
            "session_id": string("Optional session ID"),
        },
        ("amount",),
    ),
    tool(
        "browser_get_session_status",
        "Get current session info (URL, tabs, allowlist).",
        {
            "session_id": string("Optional session ID"),
        },
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
