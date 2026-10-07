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
    # System awareness (Phase 3 Step 3 — the Parietal Lobe). All read-only and all
    # gated as `system_awareness`, which auto-approves at every trust level; see
    # config/trust_levels.json for why that row is argued rather than assumed.
    #
    # They are grouped last on purpose. A user asking "what app am I in" wants one
    # answer, and a model offered 35 tools at once may pick the screenshot instead —
    # which costs a vision call, loads Moondream, and returns a description of pixels
    # rather than the window title that answers the question exactly. Listing these
    # after the pointer-and-window tools keeps the cheaper, exact answer the easy one.
    tool(
        "parietal_get_active_window",
        "Report which application window currently has focus, with its title and "
        "position. Cheaper and more exact than a screenshot for 'what app am I in'.",
        {},
    ),
    tool(
        "parietal_get_screen_info",
        "Report the display layout: total resolution, every monitor, and DPI scale.",
        {
            "monitor": integer("Optional zero-based monitor index to focus on"),
        },
    ),
    tool(
        "parietal_get_running_apps",
        "List the running applications with their memory and CPU use. Processes with "
        "no visible window are counted but not named.",
        {
            "include_hidden": {
                "type": "boolean",
                "description": "Also list processes with no visible window. Off by default: the full table is a fingerprint of the machine.",
            },
            "limit": integer("Maximum applications to return (default 50)"),
        },
    ),
    tool(
        "parietal_get_mouse_position",
        "Report where the pointer is and which monitor it is on.",
        {},
    ),
    tool(
        "parietal_detect_context",
        "Infer what the user is working on right now. Returns one of coding, "
        "browsing, writing, reading, gaming, idle or unknown, together with the "
        "evidence it was based on. 'unknown' means no rule matched, not that the "
        "user is idle.",
        {},
    ),
    tool(
        "parietal_get_snapshot",
        "Read every system-awareness signal at once: focused window, display layout, "
        "running applications, pointer position, inferred context and workspaces.",
        {},
    ),
    # Memory tools (Phase 6, the Hippocampus).
    #
    # Grouped last and after everything that acts, because memory is the one family
    # that changes nothing on the machine. Note what is absent: there is no tool to
    # erase a memory. Erasing is a deliberate act with a real date in it, and a
    # model being able to call it from a passing sentence would mean the store
    # could be emptied by an ordinary conversation. The Memory page is where
    # forgetting happens.
    tool(
        "memory_recall_similar",
        "Find earlier memories that match a question. Search is over conversations, "
        "learned tasks, preferences, mistakes and facts.",
        {
            "query": string("What to look for, in the user's own words"),
            "memory_type": enum(
                "Restrict to one kind of memory",
                "conversation",
                "task",
                "preference",
                "mistake",
                "fact",
            ),
            "limit": integer("Maximum memories to return (default 10)"),
        },
        ("query",),
    ),
    tool(
        "memory_remember_fact",
        "Remember a fact about the user or the work. Use it when the user states "
        "something that will still be true tomorrow.",
        {
            "fact": string("The fact itself, in one sentence"),
            "context": string("Where it came from, e.g. 'from the last call'"),
        },
        ("fact",),
    ),
    tool(
        "memory_remember_preference",
        "Remember a standing preference the user has stated. Use it when they say "
        "how they like something done, not for a one-off request.",
        {
            "key": string("What the preference is about, e.g. 'editor'"),
            "value": string("The preference, e.g. 'vscode'"),
        },
        ("key", "value"),
    ),
    tool(
        "memory_search_history",
        "Look through remembered items newest first. With no query it is the "
        "chronological view — 'what have we done'. With a query it is ranked — "
        "'what did we do about X'.",
        {
            "query": string("Optional words to filter by; omit for a plain listing"),
            "memory_type": enum(
                "Restrict to one kind of memory",
                "conversation",
                "task",
                "preference",
                "mistake",
                "fact",
            ),
            "limit": integer("Maximum entries to return (default 20)"),
        },
    ),
    tool(
        "memory_get_user_profile",
        "Return what ZUZZ knows about the user: their name, standing preferences and "
        "what they have been working on. Read-only and cheap; call it before asking "
        "something already answered.",
        {},
    ),
    # Habit tools (Phase 6, the Basal Ganglia).
    #
    # Every one of these is judged as routine_management. None of them runs a
    # routine's steps on its own authority: routine_execute returns a plan and the
    # caller is told what will happen, so nothing here is an unattended act.
    tool(
        "routine_list_all",
        "List the saved routines, with their triggers and the steps each one runs.",
        {
            "include_steps": enum("Include each routine's steps", "true", "false"),
        },
    ),
    tool(
        "routine_execute",
        "Work out what a saved routine would do at the current trust level. Returns "
        "every step, whether it is permitted, and which steps need the user to "
        "confirm. It does not run anything: confirm the steps first, then run them.",
        {
            "name": string("The routine's name"),
            "confirm_steps": array_of_strings(
                "Step numbers the user has approved, counted from 1"
            ),
        },
        ("name",),
    ),
    tool(
        "routine_create",
        "Save a new routine. Prefer routine_update on an existing routine — two "
        "routines cannot share a name.",
        {
            "name": string("Routine name: lowercase letters, digits and underscores"),
            "description": string("One line describing what it does"),
            "steps": {
                "type": "array",
                "description": "The steps to run, in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": string("An existing tool name to call"),
                        "params": {
                            "type": "object",
                            "description": "That tool's parameters",
                            "additionalProperties": True,
                        },
                        "why": string("What this step is for"),
                    },
                    "required": ["tool"],
                    "additionalProperties": False,
                },
            },
            "trigger": enum("When the routine should fire", "manual", "time", "app", "command"),
        },
        ("name", "steps"),
    ),
    tool(
        "routine_update",
        "Change a saved routine: its description, its trigger, its steps or whether "
        "it is enabled.",
        {
            "name": string("The routine to change"),
            "description": string("New description"),
            "steps": {
                "type": "array",
                "description": "Replacement steps, in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": string("An existing tool name to call"),
                        "params": {
                            "type": "object",
                            "description": "That tool's parameters",
                            "additionalProperties": True,
                        },
                        "why": string("What this step is for"),
                    },
                    "required": ["tool"],
                    "additionalProperties": False,
                },
            },
            "trigger": enum("When the routine should fire", "manual", "time", "app", "command"),
            "enabled": enum("Whether the routine may run", "true", "false"),
        },
        ("name",),
    ),
    tool(
        "routine_delete",
        "Delete a saved routine. This cannot be undone.",
        {
            "name": string("The routine to delete"),
        },
        ("name",),
    ),
    # Personality tools (Phase 6, the Cerebellum).
    #
    # Two read-only, one that writes a config file. The writable keys are a closed
    # set — style, humour, verbosity, greeting, signoff, quirks — so a tool call
    # can change how ZUZZ sounds and cannot change what it is called or how many
    # sentences it is allowed.
    tool(
        "personality_get_settings",
        "Return how ZUZZ is currently configured to speak: style, humour level, "
        "verbosity, greeting, sign-off and quirks.",
        {},
    ),
    tool(
        "personality_update_setting",
        "Change one personality setting. Editable: style, humor_level, verbosity, "
        "greeting, signoff, quirks. Ask the user before changing these rather than "
        "editing their voice unasked.",
        {
            "key": enum(
                "Which setting to change",
                "style",
                "humor_level",
                "verbosity",
                "greeting",
                "signoff",
                "quirks",
            ),
            "value": string("The new value"),
        },
        ("key", "value"),
    ),
    tool(
        "personality_detect_mood",
        "Read the mood of a message: neutral, frustrated, excited or tired. Returns "
        "how the reply should be shaped as well, so a reply to a frustrated user is "
        "short and leads with the answer.",
        {
            "message": string("The user's message to read"),
        },
        ("message",),
    ),
    # Coding tools (Phase 4, body/mouth/kilo).
    #
    # Five specialist agents behind ten tools. Grouped last because they are the
    # family that writes to disk, and a model offered everything at once reaches for
    # the generating tool when review was what the request needed.
    #
    # THE ONE THING TO READ BEFORE USING THESE: the generating tools write nothing.
    # kilo_generate_code, kilo_debug_error and kilo_refactor_code return a diff_id and
    # a proposal; kilo_apply_changes is a separate call that writes it, and it needs
    # a human to have said yes. That is why "approval" is a required parameter on the
    # apply tool and why it must repeat the diff_id: a run-on reply carrying twenty
    # tool calls is a real shape, and a misfire should be visible in the transcript
    # rather than something the gate has to infer.
    tool(
        "kilo_generate_code",
        "Generate code in a workspace from a description. Returns a proposal and a "
        "diff_id; it does NOT write anything. Read the diff, then use "
        "kilo_apply_changes if it is right.",
        {
            "description": string("What to build, in the user's own words"),
            "workspace": string(
                "Workspace name or absolute path; omit for the scratch workspace. "
                "Never a path inside ZUZZ's own folder."
            ),
        },
        ("description",),
    ),
    tool(
        "kilo_review_code",
        "Review a file for bugs, security problems and dead code. Static checks plus "
        "a model pass; read-only.",
        {
            "path": string("Path to the file, relative to the workspace"),
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
        ("path",),
    ),
    tool(
        "kilo_debug_error",
        "Find the root cause of an error and propose the fix. Returns the cause and, "
        "where one can be proposed, a diff_id. It does NOT write anything.",
        {
            "error": string("The traceback or error message"),
            "code": string("The offending source, if you have it"),
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
        ("error",),
    ),
    tool(
        "kilo_refactor_code",
        "Refactor a file towards a stated goal. Returns a diff_id; it does NOT write "
        "anything.",
        {
            "path": string("Path to the file, relative to the workspace"),
            "goal": string("What 'better' means here, in concrete terms"),
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
        ("path", "goal"),
    ),
    tool(
        "kilo_run_tests",
        "Run the workspace's test suite with pytest and return per-test results. This "
        "runs code, so it asks at trust levels 1 to 4.",
        {
            "path": string("Test path or pattern; omit for 'tests'"),
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
    ),
    tool(
        "kilo_preview_diff",
        "Show what the coding agents have proposed, without applying anything. Use "
        "this before kilo_apply_changes, and after any generating call.",
        {
            "diff_id": string("One proposal's id; omit for every pending proposal"),
        },
    ),
    tool(
        "kilo_apply_changes",
        "Write an approved change set to disk through ZUZZ's file layer, which "
        "snapshots every file it overwrites. Only call this after the user has seen "
        "the diff and agreed to it.",
        {
            "diff_id": string("The proposal to apply, from kilo_preview_diff"),
            "approval": string(
                "Must repeat the diff_id exactly. This is a deliberate check that "
                "the apply is aimed at the proposal the user actually saw."
            ),
        },
        ("diff_id", "approval"),
    ),
    tool(
        "kilo_rollback",
        "Undo an applied change set, restoring each file's previous contents. Files "
        "the change set created are moved to the recycle bin.",
        {
            "diff_id": string("The applied proposal to undo"),
        },
        ("diff_id",),
    ),
    tool(
        "kilo_git_status",
        "Read a workspace's repository status: branch, and the staged, unstaged and "
        "untracked paths. Read-only, and it cannot become a write.",
        {
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
    ),
    tool(
        "kilo_install_package",
        "Install a Python package into a workspace with pip. Ask the user first: this "
        "asks for confirmation at every trust level, including 5, because a wheel "
        "runs its author's build code.",
        {
            "name": string("Package name, optionally with a version, e.g. 'flask>=3'"),
            "workspace": string("Workspace name or path; omit for the scratch workspace"),
        },
        ("name",),
    ),
    # Communication tools (Phase 8, the Communication Hub).
    #
    # Grouped last because these send data off the machine. The send_messages row
    # in trust_levels.json asks at every level, so every tool here prompts below 5.
    tool(
        "telegram_send_message",
        "Send a Telegram message to the configured chat.",
        {
            "message": string("The message text"),
            "buttons": array_of_strings("Optional button labels"),
            "chat_id": string("Optional chat ID override"),
        },
        ("message",),
    ),
    tool(
        "telegram_send_file",
        "Send a file via Telegram to the configured chat.",
        {
            "file_path": string("Absolute path to the file to send"),
            "caption": string("Optional caption"),
        },
        ("file_path",),
    ),
    tool(
        "telegram_request_approval",
        "Send an approval request via Telegram and wait for a response.",
        {
            "action": string("What needs approval"),
            "timeout_seconds": integer("How long to wait in seconds"),
        },
        ("action",),
    ),
    tool(
        "email_send",
        "Send an email via the configured SMTP account.",
        {
            "subject": string("Email subject"),
            "body": string("Email body"),
            "to": string("Optional recipient email address"),
        },
        ("subject", "body"),
    ),
    tool(
        "email_schedule_briefing",
        "Schedule a daily email briefing.",
        {
            "subject": string("Briefing subject line"),
            "summary": string("One-line summary"),
            "time": string("When to send, e.g. '08:00'"),
        },
        ("subject", "time"),
    ),
    tool(
        "email_check_inbox",
        "Check the configured email inbox for new messages.",
        {
            "limit": integer("Max messages to return"),
        },
    ),
    tool(
        "push_send_notification",
        "Send a push notification via ntfy.",
        {
            "title": string("Notification title"),
            "message": string("Notification body"),
            "priority": enum("Priority level", "low", "normal", "high", "urgent"),
        },
        ("title", "message"),
    ),
    tool(
        "push_set_quiet_hours",
        "Set quiet hours when push notifications are suppressed.",
        {
            "start": string("Quiet hours start, e.g. 22:00"),
            "end": string("Quiet hours end, e.g. 07:00"),
        },
        ("start", "end"),
    ),
    # Background agent tools (Phase 8, the Background Agents).
    #
    # deploy_agents asks below 4, where the user has not yet said they are happy
    # for ZUZZ to spin up remote compute on their behalf. The remaining tools
    # auto-approve from 3 because they read state or schedule work rather than
    # create it.
    tool(
        "agent_deploy",
        "Deploy a background cloud agent to Google Colab.",
        {
            "agent_type": string("Type of agent: email_monitor, price_watcher, research_agent, news_aggregator, system_monitor, or custom"),
            "name": string("Human-readable name"),
            "config": string("JSON config string"),
        },
        ("agent_type",),
    ),
    tool(
        "agent_stop",
        "Stop a running background agent.",
        {
            "agent_id": string("The agent to stop"),
        },
        ("agent_id",),
    ),
    tool(
        "agent_list",
        "List all deployed background agents.",
        {},
    ),
    tool(
        "agent_schedule",
        "Schedule a background agent to run on a cron.",
        {
            "agent_id": string("The agent to schedule"),
            "cron_expression": string("Cron expression, e.g. */15 * * * *"),
        },
        ("agent_id",),
    ),
    tool(
        "agent_get_results",
        "Get the latest results from a background agent.",
        {
            "agent_id": string("The agent to fetch results from"),
        },
        ("agent_id",),
    ),
    tool(
        "agent_create_custom",
        "Create a custom background agent from Python code.",
        {
            "name": string("Agent name"),
            "code": string("Python source code for the agent"),
            "config": string("Optional JSON config string"),
        },
        ("name", "code"),
    ),
    tool(
        "agent_sync_now",
        "Sync results from a remote background agent immediately.",
        {
            "agent_id": string("The agent to sync"),
        },
        ("agent_id",),
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
