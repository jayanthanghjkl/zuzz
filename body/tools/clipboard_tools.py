"""Body/Tools — the clipboard.

Reading and writing the clipboard, reached from a chat request.

The read is the reason this is not in :mod:`body.tools.input_tools`. Its
category follows the *content*, not the tool: a clipboard holding something
shaped like a credential is decided as ``access_credentials``, which asks at
every trust level. The probe happens during resolution, before the gate is
consulted, so the gate is asked about ``access_credentials`` rather than about
``read_files`` and then handed back a secret — the same ordering as
``/api/hands/clipboard``, and for the same reason.

The probe reads the clipboard without a decision, which is safe precisely
because it does not *return* it: the text goes to
:func:`~body.hands.text_scanner.looks_like_credential` and the answer that
leaves this module is a category and a boolean, never the content. What the user
sees is still gated.

A write is a plain ``write_files`` decision, and the Body re-scans the text on
the way through — so a model asked to type or paste a credential is refused by
the keyboard guards even at a trust level that would have approved the write.
"""

from __future__ import annotations

from typing import Any, Dict

from body.hands import action_classifier
from body.hands.text_scanner import looks_like_credential
from body.tools.tool_spec import (
    ToolContext,
    ToolSpec,
    ToolTarget,
    bounded_text,
    required_text,
)


def _probe_text(context: ToolContext) -> str:
    """Return the clipboard's current text without deciding anything.

    ``Clipboard``'s public entry point is :meth:`~body.hands.clipboard.Clipboard.read`,
    which takes a decision — and the whole point of this probe is that the
    decision depends on what the clipboard holds. So it reads through
    ``_read_text``, the same private path ``GET /api/hands/clipboard`` uses, which
    returns ``""`` rather than raising when the clipboard holds an image or a file
    list. Verified against the class rather than assumed: the public surface is
    ``backend``, ``read``, ``write`` and ``clear`` only.

    Args:
        context: The collaborators, carrying the Hands facade.

    Returns:
        The clipboard text, or an empty string when there is none to read.

    Raises:
        ToolRefused: If the Hands facade has no clipboard to read, which means the
            context was built without one rather than that the clipboard is empty.
    """
    clipboard = getattr(getattr(context, "hands", None), "clipboard", None)
    probe = getattr(clipboard, "_read_text", None)
    if probe is None:
        raise ToolRefused(
            "the clipboard is not attached to this session, so there is "
            "nothing to read"
        )
    return probe()


def _resolve_clipboard_read(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a clipboard read, escalating when the content looks like a secret."""
    finding = looks_like_credential(_probe_text(context))
    preview: Dict[str, Any] = {"description": "Read the clipboard"}
    if finding:
        preview["warning"] = (
            "the clipboard holds something shaped like a credential, so this "
            "reads as access to a secret and asks at every trust level"
        )
    return ToolTarget(
        category=action_classifier.clipboard_read_category(finding),
        target="clipboard",
        preview=preview,
    )


def _execute_clipboard_read(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Read the clipboard through the Hands facade with an allowed decision."""
    return context.hands.clipboard_read(decision)


def _resolve_clipboard_write(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve clipboard contents into the sample the gate judges."""
    text = required_text(params, "text", "clipboard_write")
    return ToolTarget(
        category=action_classifier.category_for("clipboard_write"),
        target=bounded_text(params, "text", "clipboard_write"),
        preview={
            "description": f"Put {len(text)} characters on the clipboard",
            "text": text[:200],
        },
    )


def _execute_clipboard_write(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Write the clipboard through the Hands facade with an allowed decision."""
    return context.hands.clipboard_write(decision, str(params["text"]))


#: The clipboard tools.
CLIPBOARD_TOOLS: tuple = (
    ToolSpec(
        name="clipboard_read",
        summary="Read the clipboard",
        resolve=_resolve_clipboard_read,
        execute=_execute_clipboard_read,
        returns="The clipboard text",
    ),
    ToolSpec(
        name="clipboard_write",
        summary="Replace the clipboard contents",
        resolve=_resolve_clipboard_write,
        execute=_execute_clipboard_write,
        returns="Confirmation that the clipboard was set",
    ),
)
