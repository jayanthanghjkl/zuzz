"""Body/Tools — files and folders.

Reading, writing, deleting, listing and searching, reached from a chat request.

Every path is resolved through :func:`~body.fingers.guards.resolve_target`
before the gate is consulted, which expands it to an absolute form, follows one
symlink and refuses a relative path that climbs out of the project. Two
consequences, both of them the point of resolving early:

* the gate decides about ``C:\\Users\\me\\notes.txt`` rather than about
  ``notes.txt``, so the decision and the write name the same file;
* a symlink inside the project pointing at ``C:\\Windows`` is classified as a
  Windows file, because that is what it is. The Body classifies it again
  before the write — this is the gate knowing, not the Body trusting.

The categories come from :func:`~body.fingers.guards.category_for`, the same
mapping the file routes use, so a chat-driven delete and a Desktop-tab delete of
the same path are decided identically. Nothing here bypasses that to make a
deletion easier to ask for.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from body.fingers import guards
from body.fingers.exceptions import BodyRefused
from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)

#: Longest file content returned to the model. ``FileManager.read`` will hand
#: back five megabytes; putting that in a chat reply would be a denial of
#: service against the context window, and a model cannot use it anyway. The
#: truncation is reported rather than passed off as the whole file.
MAX_REPORTED_CHARS = 20_000


def _resolve_path(key: str, operation: str) -> Any:
    """Return a resolved absolute path, or refuse.

    Args:
        key: The parameter holding the path.
        operation: The Body operation name, for its category.

    Returns:
        The resolved absolute path.

    Raises:
        ToolRefused: If the path is empty, escapes the project, or the Body's
            own path resolution refuses it.
    """
    def _resolve(params: Dict[str, Any], context: ToolContext, _key: str = key) -> ToolTarget:
        """Build the ToolTarget for this path."""
        path = required_text(params, _key, operation)
        try:
            resolved = guards.resolve_target(path)
        except BodyRefused as exc:
            raise ToolRefused(str(exc)) from exc
        return ToolTarget(
            category=guards.category_for(operation),
            target=resolved,
            preview={
                "description": f"{operation} {resolved}",
                "path": resolved,
            },
            detail={"requested": path},
        )

    return _resolve


def _execute_read(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Read the file through the Fingers facade with an allowed decision."""
    text = context.files.read(decision, str(params["path"]))
    if not isinstance(text, str):
        return {"bytes": len(text), "truncated": True, "note": "binary file"}
    if len(text) <= MAX_REPORTED_CHARS:
        return {"content": text, "chars": len(text)}
    return {
        "content": text[:MAX_REPORTED_CHARS],
        "chars": len(text),
        "truncated": True,
        "note": (
            f"showing the first {MAX_REPORTED_CHARS} of {len(text)} characters; "
            "read it in ranges or search inside it instead"
        ),
    }


def _execute_write(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Write the file through the Fingers facade with an allowed decision.

    The missing parent folders are created first, which is what the tool's schema
    promises the model — ``file_write`` is documented as "creating it and any
    missing parent folders", and ``FileManager.write`` deliberately does not:
    the Body treats ``mkdir`` as its own gated operation, and a write silently
    growing a directory tree would make a file write capable of creating
    directories nobody approved.

    Creating them here, rather than in the Body, keeps that contract intact while
    making the schema true. It is safe because :func:`~body.fingers.guards.resolve_target`
    already ran during resolution and refused any path that escapes the project or
    lands in a protected tree — so the parent has passed the guards, and the
    ``write_files`` decision the Body re-verifies is about this exact file under
    this exact path.

    Args:
        decision: An allowed ``write_files`` decision.
        params: The validated parameters.
        context: The collaborators.

    Returns:
        Whatever the Body reports for the write.

    Raises:
        ToolRefused: If the parent folder cannot be created. Refused rather than
            left to raise: "I could not create that folder" is an answer, and an
            ``OSError`` escaping here would surface as an internal error with the
            real cause only in the log.
    """
    path = str(params["path"])
    parent = os.path.dirname(guards.resolve_target(path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise ToolRefused(
                f"file_write: could not create the folder {parent} — {exc.strerror}"
            ) from exc
    return context.files.write(decision, path, str(params["content"]))


def _execute_delete(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Move the file to the trash through the Fingers facade.

    Never a hard delete. The Body has no destructive path to offer, and a chat
    reply is a worse place to be casual about a deletion than a button is.
    """
    return context.files.delete(decision, str(params["path"]))


def _execute_list(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """List the folder through the Fingers facade with an allowed decision."""
    return context.directories.list(decision, str(params["path"]))


def _execute_search(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Search under the root through the Fingers facade with an allowed decision."""
    return context.directories.search(
        decision, str(params["pattern"]), str(params["root"])
    )


def _resolve_search(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a search's root, keeping the pattern as the judged target.

    The root is what the search will walk, so it is the root that is resolved
    and judged. The pattern travels in the preview and the detail, because it is
    a filter rather than the thing being touched — and because a credential
    pattern is something the user asked to look for, not a file to open.
    """
    pattern = required_text(params, "pattern", "file_search")
    root = required_text(params, "root", "file_search")
    try:
        resolved = guards.resolve_target(root)
    except BodyRefused as exc:
        raise ToolRefused(str(exc)) from exc
    return ToolTarget(
        category=guards.category_for("search"),
        target=resolved,
        preview={
            "description": f"Search {resolved} for {pattern!r}",
            "root": resolved,
            "pattern": pattern,
        },
        detail={"pattern": pattern},
    )


#: The file and folder tools.
FILE_TOOLS: tuple = (
    ToolSpec(
        name="file_read",
        summary="Read a text file",
        resolve=_resolve_path("path", "read"),
        execute=_execute_read,
        returns="The file's contents, truncated if very long",
    ),
    ToolSpec(
        name="file_write",
        summary="Write a text file",
        resolve=_resolve_path("path", "write"),
        execute=_execute_write,
        returns="The path written and how many bytes",
    ),
    ToolSpec(
        name="file_delete",
        summary="Delete a file or folder, reversibly",
        resolve=_resolve_path("path", "delete"),
        execute=_execute_delete,
        returns="The trash id the item can be restored from",
    ),
    ToolSpec(
        name="file_list",
        summary="List a folder",
        resolve=_resolve_path("path", "list"),
        execute=_execute_list,
        returns="The entries, their sizes and whether more remain",
    ),
    ToolSpec(
        name="file_search",
        summary="Find files by name under a folder",
        resolve=_resolve_search,
        execute=_execute_search,
        returns="The matching paths",
    ),
)
