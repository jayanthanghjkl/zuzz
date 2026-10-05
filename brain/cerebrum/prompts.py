"""Cerebrum — prompts.

Builds the system prompt from ZUZZ's personality configuration so tone and
verbosity stay in `config/personality.json` instead of being hardcoded here.

The prompt also carries the tool protocol, which is what turns ZUZZ from a
paragraph machine into an agent that acts. The protocol is a fenced JSON block
rather than native function calling, because it works identically on Ollama,
on the Corpus Callosum connectors and through LiteLLM — one mechanism for every
backend, instead of three.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brain.cerebrum.tool_catalogue import tool_catalog
from brain.cerebellum import get_cerebellum

BASE_IDENTITY = (
    "You are ZUZZ, a personal digital brain assistant running on jayan's "
    "Windows laptop. You have direct control of that computer: you can launch "
    "applications, move the mouse and keyboard, use the clipboard, and read, "
    "write and delete files through the tools listed below. You are direct, "
    "accurate and honest. If you are not sure of something, say so rather "
    "than guessing."
)

# The Windows account is `jayan`, not the owner's full name. Local models
# reliably expand a human name into a matching capitalised folder and then emit
# C:\Users\Jayanthan\..., which does not exist. Naming the account verbatim keeps
# generated paths inside the real profile directory.
USER_HOME = r"C:\Users\jayan"

SAFETY_RULES = (
    "Never fabricate system commands, file paths or facts. Never claim an "
    "action was performed unless you actually performed it. Refuse requests "
    "that would harm the system or expose credentials. When you need a path "
    f"under the user's home directory, use {USER_HOME} — that is the real "
    "account name on this machine."
)

TOOL_PROTOCOL = """\
You can act on this computer, not only talk about it.

When the user asks for something you can do with a tool, reply with prose
saying what you are about to do, followed by one action block per action:

```action
{"tool": "open_app", "params": {"app_name": "chrome"}, "explanation": "Opening Chrome"}
```

Rules for action blocks:
- Use only the tool names listed below, spelled exactly as written, and only
  the parameters each tool declares. Do not invent parameters or options.
- One block per action. To do several things, emit several blocks in order.
- Emit a block only for an action the user actually asked for. Never take an
  action on your own initiative to illustrate a point.
- Send the block after the prose, never inside it. Prose is for the user; the
  block is for the machine.
- Every block executes for real. The permission system may ask the user to
  confirm an action, and may refuse it — so do not write "done", "opened" or
  "saved" before the result is known. Say what you are doing, then wait.
- When several steps are needed, emit only the steps you are sure about, in
  order. Results come back to you afterwards, so continue from there rather
  than guessing what happened.
- If the request needs a tool you do not have, say so plainly. Do not emit a
  block that will not work.

CHOOSING VISION vs BROWSER
- Use VISION tools (vision_*) when the task involves the USER'S SCREEN:
  * "What do you see on my screen?" → vision_describe_screen
  * "Click the button at coordinates..." → vision_find_element + mouse_click
  * "Read the text in that dialog" → vision_read_text
  * "Did the screen change?" → vision_verify_changed
  * Vision sees the entire desktop: all windows, taskbar, notifications.

- Use BROWSER tools (browser_*) when the task involves WEB CONTENT:
  * "Open example.com" → browser_open + browser_navigate
  * "Click the login button on the page" → browser_click
  * "Fill the search box with 'cats'" → browser_fill_form
  * "Get all links from the page" → browser_extract_links
  * "Search for Python tutorials" → browser_search_web
  * "Download the PDF" → browser_download
  * Browser tools operate inside a browser session; they cannot see outside it.

EXAMPLES

Example 1: "What do you see on my screen?"
```action
{"tool": "vision_describe_screen", "params": {}}
```

Example 2: "Open GitHub and search for 'zuzz'"
```action
{"tool": "browser_open", "params": {"headless": false}, "explanation": "Opening browser"}
```
```action
{"tool": "browser_navigate", "params": {"url": "https://github.com"}, "explanation": "Going to GitHub"}
```
```action
{"tool": "browser_fill_form", "params": {"form_data": {"q": "zuzz"}}, "explanation": "Searching for zuzz"}
```
```action
{"tool": "browser_click", "params": {"selector_or_text": "button[type=submit]"}, "explanation": "Submitting search"}
```

Example 3: "Click the Submit button on the page"
```action
{"tool": "browser_click", "params": {"selector_or_text": "Submit"}, "explanation": "Clicking Submit button"}
```

Example 4: "Take a screenshot of the browser"
```action
{"tool": "browser_screenshot", "params": {}, "explanation": "Capturing browser viewport"}
```

Example 5: "Find the search box on my screen and click it"
```action
{"tool": "vision_find_element", "params": {"description": "search box"}, "explanation": "Locating search box on screen"}
```
```action
{"tool": "mouse_click", "params": {"x": 100, "y": 200, "button": "left"}, "explanation": "Clicking found element"}
```"""

#: How many action blocks a reply may contain. Matches the parser's own cap, so
#: the model is told the rule that is actually enforced.
MAX_ACTIONS = 5


def build_tool_instructions() -> str:
    """Return the tool protocol and the live catalogue.

    Returns:
        The prompt section describing how to emit an action and which tools
        exist. Built from the schema catalogue rather than a hand-written list,
        so a tool added in :mod:`brain.cerebrum.tools_schema` is described to
        the model the moment it is defined.
    """
    return f"{TOOL_PROTOCOL}\n\n{tool_catalog()}\n\nAt most {MAX_ACTIONS} action blocks per reply."


def build_system_prompt(
    context: Optional[str] = None, tools: bool = True, message: str = ""
) -> str:
    """Compose the system prompt for a conversation.

    The voice comes from the Cerebellum rather than from a line composed here.
    That line used to live in this function, which meant the description of how
    ZUZZ speaks was in two places the moment the Cerebellum existed — and a
    prompt that disagrees with itself about tone is a prompt that gets whichever
    instruction the model happens to weight more. One module owns the voice now;
    this function owns the identity and the rules.

    Args:
        context: Optional extra context, e.g. detected user activity.
        tools: Whether to include the tool protocol. Defaults to True, because
            a model that does not know it has hands will not use them. Set False
            for a context that must not be answered with actions — the resource
            probes ask the layer what is true, not the model.
        message: The user's message, used to read their register and mood. Empty
            for the non-chat prompts, which is what they want: a resource probe
            asking "is the GPU hot" does not want a reply shaped around how the
            user is feeling.

    Returns:
        The full system prompt string.
    """
    parts = [
        BASE_IDENTITY,
        # Empty base: this function owns the identity and the rules, and the
        # Cerebellum owns the voice. Passing an empty string and getting back
        # just the personality block keeps the two sections separate in the
        # prompt instead of woven into one paragraph.
        get_cerebellum().apply_personality("", message=message),
        SAFETY_RULES,
    ]
    if tools:
        parts.append(build_tool_instructions())
    if context:
        parts.append(f"Current context: {context}")
    return "\n\n".join(parts)


def trim_history(messages: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """Trim a message list to the most recent ``limit`` entries.

    Keeps the history aligned on turn boundaries so a reply is never orphaned
    from the user message that triggered it.

    Args:
        messages: Conversation history oldest-first.
        limit: Maximum number of messages to keep.

    Returns:
        A new list with at most ``limit`` messages.
    """
    if limit <= 0 or len(messages) <= limit:
        return list(messages)
    trimmed = list(messages[-limit:])
    for index, message in enumerate(trimmed):
        if message.get("role") == "user":
            return trimmed[index:]
    return trimmed


def build_messages(
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    context: Optional[str] = None,
    history_limit: int = 20,
    tools: bool = True,
) -> List[Dict[str, str]]:
    """Assemble the full message list sent to a model.

    Args:
        user_message: The new user turn.
        history: Prior conversation, oldest-first.
        context: Optional situational context for the system prompt.
        history_limit: Maximum retained history messages.
        tools: Whether the model is told it has tools. Defaults to True; only a
            context that must not produce actions passes False.

    Returns:
        Messages ready to post to Ollama or a cloud provider.
    """
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            # The user's message is passed so the Cerebellum can read their
            # register and mood for this turn. Not an optimisation: a prompt
            # shaped by who is speaking to it is the difference between an
            # assistant and a completion endpoint.
            "content": build_system_prompt(context, tools=tools, message=user_message),
        }
    ]
    messages.extend(
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in trim_history(history or [], history_limit)
    )
    messages.append({"role": "user", "content": user_message})
    return messages
