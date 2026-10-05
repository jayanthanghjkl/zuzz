"""Body/Tools — the coding agents.

Ten tools over :mod:`body.mouth.kilo`, reached from a chat request.

**Nothing here writes except ``kilo_apply_changes`` and ``kilo_rollback``.** The
generating tools return a *proposal* with a ``diff_id``; the tool that writes is a
separate call, under ``modify_project``, and it needs a second decision the caller
already holds — a ``write_files`` decision naming the workspace root, exactly as
``browser_download`` needs one for its quarantine path.

**Resolution runs before the decision and refuses what the guard would refuse.**
:meth:`KiloEngine.propose` and :meth:`KiloWorkspace.resolve` both run in ``resolve``,
so the absolute workspace path the gate decides about is the same string the workspace
guard will insist on when the tool executes. A decision built from a hand-written
target is refused by the Body's own replay check, which is correct behaviour and a
miserable thing to debug.

**Two of these are gauges and are filed as ``read_files``.** ``kilo_preview_diff`` and
``kilo_git_status`` report ZUZZ's own pending state and the repository's metadata.
They are not ``generate_code`` or ``git_operations`` because neither of those rows
means "look at something", and asking about them would be exactly the over-prompting
that ``visual_capture`` and ``memory_access`` are documented to avoid.

``kilo_install_package`` is the one tool that reaches ``install_packages``, which asks
at **every** trust level including 5 (immutable rule R7). From a chat that means the
user sees a prompt before any package is fetched, which is the correct outcome and not
a bug to be worked around.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.prefrontal_cortex.action import ActionCategory

from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)

#: The scratch workspace a request without one gets.
DEFAULT_WORKSPACE = ""


def _engine(context: ToolContext, tool: str):
    """Return the Kilo engine, refusing when it is not attached.

    Args:
        context: The collaborators the tool may act through.
        tool: The tool name, for the message.

    Returns:
        The :class:`~body.mouth.kilo.kilo_engine.KiloEngine`.

    Raises:
        ToolRefused: When ``context.kilo`` is None. Answering "I cannot write code"
            from an engine that was never wired would be a refusal the model may
            report as "ZUZZ tried and could not", which is the confident wrong answer
            every other tool family here guards against.
    """
    engine = context.kilo
    if engine is None:
        raise ToolRefused(
            f"{tool}: the coding agents are not attached, so ZUZZ cannot use them. "
            "Say so rather than inventing a result."
        )
    return engine


def _workspace(params: Dict[str, Any], context: ToolContext, tool: str) -> Any:
    """Resolve the workspace a call names, before any decision exists.

    Args:
        params: The validated parameters.
        context: The collaborators.
        tool: The tool name, for the message.

    Returns:
        The :class:`~body.mouth.kilo.kilo_workspace.Workspace`.

    Raises:
        ToolRefused: The workspace is ZUZZ's own tree, or is not one ZUZZ has been
            told about. Both happen before the gate is consulted, so they are
            precheck refusals rather than verdicts.
    """
    wanted = str(params.get("workspace", DEFAULT_WORKSPACE) or DEFAULT_WORKSPACE)
    return _engine(context, tool).workspace.resolve(wanted)


def _text(params: Dict[str, Any], key: str, tool: str, limit: int = 4000) -> str:
    """Return a bounded string parameter, refusing a blank one.

    Args:
        params: The validated parameters.
        key: The parameter name.
        tool: The tool name, for the message.
        limit: How much of the value to keep. A whole file's contents would both
            swamp the Amygdala's pattern matching on the target and put the user's
            data into every threat log.

    Returns:
        The stripped, truncated value.

    Raises:
        ToolRefused: If the parameter is missing, not a string, or blank.
    """
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolRefused(f"{tool}: {key!r} is empty, so there is nothing to act on")
    return value.strip()[:limit]


def _optional_text(params: Dict[str, Any], key: str, tool: str, limit: int = 4000) -> str:
    """Return a bounded string parameter that is allowed to be absent.

    A parameter the schema marks optional has to be optional here too. ``code`` on
    ``kilo_debug_error`` is the case: a user pasting a traceback without the file is
    the ordinary situation, and refusing it would mean the tool answered only the
    requests that happened to include the source — which reads as the agent being
    unhelpful rather than as a deliberate narrowing.

    Args:
        params: The validated parameters.
        key: The parameter name.
        tool: The tool name, for the message.
        limit: How much of the value to keep.

    Returns:
        The stripped, truncated value, or an empty string when it was not supplied.
    """
    value = params.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolRefused(f"{tool}: {key!r} must be text")
    return value.strip()[:limit]


# -- kilo_generate_code ------------------------------------------------------


def _resolve_generate(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a generation request into the workspace the gate judges."""
    space = _workspace(params, context, "kilo_generate_code")
    description = _text(params, "description", "kilo_generate_code", 2000)
    return ToolTarget(
        category=str(ActionCategory.GENERATE_CODE),
        target=space.root,
        preview={
            "description": "Generate code in the workspace",
            "workspace": space.root,
            "request": description,
            "note": "produces a diff to review; it writes nothing until you apply it",
        },
    )


async def _execute_generate(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Run the Architect and the Coder, and return the proposal."""
    engine = _engine(context, "kilo_generate_code")
    return await engine.generate(
        decision,
        _text(params, "description", "kilo_generate_code", 4000),
        str(params.get("workspace", "") or ""),
    )


# -- kilo_review_code --------------------------------------------------------


def _resolve_review(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a review into the exact file the gate judges."""
    space = _workspace(params, context, "kilo_review_code")
    path = required_text(params, "path", "kilo_review_code")
    return ToolTarget(
        category=str(ActionCategory.READ_FILES),
        target=space.path(path),
        preview={"description": f"Review {path}", "workspace": space.root},
    )


async def _execute_review(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Review the file, statically and with the model."""
    engine = _engine(context, "kilo_review_code")
    return await engine.review(
        decision, required_text(params, "path", "kilo_review_code"),
        str(params.get("workspace", "") or ""),
    )


# -- kilo_debug_error --------------------------------------------------------


def _resolve_debug(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a debug request into the workspace the gate judges."""
    space = _workspace(params, context, "kilo_debug_error")
    error = _text(params, "error", "kilo_debug_error", 1000)
    return ToolTarget(
        category=str(ActionCategory.GENERATE_CODE),
        target=space.root,
        preview={
            "description": "Find the cause of an error and propose a fix",
            "workspace": space.root, "error": error,
            "note": "proposes a diff; it writes nothing until you apply it",
        },
    )


async def _execute_debug(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Ask the Debugger for a cause and, where it produces one, a fix."""
    engine = _engine(context, "kilo_debug_error")
    return await engine.debug(
        decision,
        _text(params, "error", "kilo_debug_error", 8000),
        _optional_text(params, "code", "kilo_debug_error", 20000),
        str(params.get("workspace", "") or ""),
    )


# -- kilo_refactor_code ------------------------------------------------------


def _resolve_refactor(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a refactor into the exact file the gate judges."""
    space = _workspace(params, context, "kilo_refactor_code")
    path = required_text(params, "path", "kilo_refactor_code")
    return ToolTarget(
        category=str(ActionCategory.GENERATE_CODE),
        target=space.root,
        preview={
            "description": f"Refactor {path}",
            "workspace": space.root,
            "goal": _text(params, "goal", "kilo_refactor_code", 500),
            "note": "proposes a diff; it writes nothing until you apply it",
        },
    )


async def _execute_refactor(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Refactor the file against the stated goal."""
    engine = _engine(context, "kilo_refactor_code")
    return await engine.refactor(
        decision,
        required_text(params, "path", "kilo_refactor_code"),
        _text(params, "goal", "kilo_refactor_code", 2000),
        str(params.get("workspace", "") or ""),
    )


# -- kilo_run_tests ----------------------------------------------------------


def _resolve_tests(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a test run into the workspace the gate judges."""
    space = _workspace(params, context, "kilo_run_tests")
    return ToolTarget(
        category=str(ActionCategory.EXECUTE_CODE),
        target=space.root,
        preview={
            "description": "Run the test suite",
            "workspace": space.root,
            "target": str(params.get("path", "tests") or "tests"),
            "note": "runs code; this asks at levels 1 to 4",
        },
    )


def _execute_tests(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Run pytest and return the parsed results."""
    engine = _engine(context, "kilo_run_tests")
    return engine.run_tests(
        decision, str(params.get("workspace", "") or ""),
        str(params.get("path", "tests") or "tests"),
    )


# -- kilo_preview_diff -------------------------------------------------------


def _resolve_preview(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a preview request. A gauge, so its target is the proposal id."""
    return ToolTarget(
        category=str(ActionCategory.READ_FILES),
        target=str(params.get("diff_id", "") or "kilo:preview"),
        preview={"description": "Show a proposed change set without applying it"},
    )


def _execute_preview(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return one proposal, or all pending ones."""
    return _engine(context, "kilo_preview_diff").preview_diff(
        str(params.get("diff_id", "") or "")
    )


# -- kilo_apply_changes ------------------------------------------------------


def _resolve_apply(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve an apply request into the proposal the gate judges."""
    engine = _engine(context, "kilo_apply_changes")
    diff_id = required_text(params, "diff_id", "kilo_apply_changes")
    proposal = engine.preview_diff(diff_id)
    return ToolTarget(
        category=str(ActionCategory.MODIFY_PROJECT),
        target=space_root_of(proposal),
        preview={
            "description": f"Apply {proposal.get('files', 0)} file change(s)",
            "diff_id": diff_id,
            "workspace": proposal.get("workspace", ""),
            "additions": proposal.get("additions", 0),
            "deletions": proposal.get("deletions", 0),
        },
        detail={"diff_id": diff_id},
    )


def _execute_apply(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Write the proposal through Body/Fingers."""
    engine = _engine(context, "kilo_apply_changes")
    diff_id = required_text(params, "diff_id", "kilo_apply_changes")
    # A model asked for "apply" once and a run-on reply carrying twenty tool calls
    # is a real shape. Requiring it to echo the exact id back makes a misfire
    # visible in the transcript rather than something the gate has to guess at.
    if str(params.get("approval", "")).strip().lower() != diff_id.strip().lower():
        raise ToolRefused(
            f"kilo_apply_changes: 'approval' must repeat the diff_id exactly "
            f"({diff_id}), so that an apply is deliberate. Nothing was written."
        )
    write_decision: Optional[Any] = getattr(context, "write_decision", None)
    if write_decision is None:
        raise ToolRefused(
            "kilo_apply_changes needs a write_files decision in context.write_decision "
            "covering the workspace root. Minting one here would be the Body "
            "granting itself permission, so it is refused instead. Apply from the "
            "Code page, where the confirmation mints it."
        )
    return engine.apply(decision, write_decision, diff_id, force=False)


def space_root_of(proposal: Dict[str, Any]) -> str:
    """Return the workspace root a proposal belongs to.

    Args:
        proposal: The proposal record.

    Returns:
        Its workspace root, or the proposal id when the record has none.
    """
    return str(proposal.get("workspace") or proposal.get("diff_id") or "kilo:apply")


# -- kilo_rollback -----------------------------------------------------------


def _resolve_rollback(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a rollback into the proposal the gate judges."""
    engine = _engine(context, "kilo_rollback")
    diff_id = required_text(params, "diff_id", "kilo_rollback")
    proposal = engine.preview_diff(diff_id)
    return ToolTarget(
        category=str(ActionCategory.MODIFY_PROJECT),
        target=space_root_of(proposal),
        preview={"description": f"Roll back {diff_id}", "diff_id": diff_id,
                 "workspace": proposal.get("workspace", "")},
        detail={"diff_id": diff_id},
    )


def _execute_rollback(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Undo the proposal through Body/Fingers."""
    engine = _engine(context, "kilo_rollback")
    write_decision: Optional[Any] = getattr(context, "write_decision", None)
    if write_decision is None:
        raise ToolRefused(
            "kilo_rollback needs a write_files decision in context.write_decision "
            "covering the workspace root. Roll back from the Code page, where the "
            "confirmation mints it."
        )
    return engine.rollback(decision, write_decision, required_text(params, "diff_id", "kilo_rollback"))


# -- kilo_git_status ---------------------------------------------------------


def _resolve_git_status(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a git status request. A gauge, so it is a read."""
    space = _workspace(params, context, "kilo_git_status")
    return ToolTarget(
        category=str(ActionCategory.READ_FILES),
        target=space.root,
        preview={"description": "Read the repository status", "workspace": space.root},
    )


def _execute_git_status(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the workspace's porcelain status, parsed."""
    return _engine(context, "kilo_git_status").git_status(
        str(params.get("path", "") or str(params.get("workspace", "") or ""))
    )


# -- kilo_install_package ----------------------------------------------------


def _resolve_install(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve an install request into the package the gate judges."""
    space = _workspace(params, context, "kilo_install_package")
    name = required_text(params, "name", "kilo_install_package")
    return ToolTarget(
        category=str(ActionCategory.INSTALL_PACKAGES),
        target=space.root,
        preview={
            "description": f"Install {name}",
            "workspace": space.root, "package": name,
            "note": "this asks at every trust level, including 5",
        },
        detail={"package": name},
    )


def _execute_install(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Install the package into the workspace's environment."""
    return _engine(context, "kilo_install_package").install_package(
        decision, required_text(params, "name", "kilo_install_package"),
        str(params.get("workspace", "") or ""),
    )


#: The coding tools, in catalogue order.
CODE_TOOLS: tuple = (
    ToolSpec(
        name="kilo_generate_code",
        summary="Generate code in a workspace as a reviewable proposal",
        resolve=_resolve_generate, execute=_execute_generate,
        returns="A diff_id and the proposed files. Nothing is written yet.",
    ),
    ToolSpec(
        name="kilo_review_code",
        summary="Review a file for bugs, security problems and dead code",
        resolve=_resolve_review, execute=_execute_review,
        returns="Findings with file:line, sorted most severe first",
    ),
    ToolSpec(
        name="kilo_debug_error",
        summary="Find the root cause of an error and propose the fix",
        resolve=_resolve_debug, execute=_execute_debug,
        returns="The cause, and a diff_id when a fix can be proposed",
    ),
    ToolSpec(
        name="kilo_refactor_code",
        summary="Refactor a file towards a stated goal",
        resolve=_resolve_refactor, execute=_execute_refactor,
        returns="A diff_id and the proposed file",
    ),
    ToolSpec(
        name="kilo_run_tests",
        summary="Run the workspace's test suite",
        resolve=_resolve_tests, execute=_execute_tests,
        returns="Per-test outcomes and the failing node ids",
    ),
    ToolSpec(
        name="kilo_preview_diff",
        summary="Show a proposed change set without applying it",
        resolve=_resolve_preview, execute=_execute_preview,
        returns="Every pending proposal, or one by diff_id",
    ),
    ToolSpec(
        name="kilo_apply_changes",
        summary="Write an approved change set through Body/Fingers",
        resolve=_resolve_apply, execute=_execute_apply,
        returns="The files written, with their .bak.1 backups",
    ),
    ToolSpec(
        name="kilo_rollback",
        summary="Undo an applied change set",
        resolve=_resolve_rollback, execute=_execute_rollback,
        returns="What was restored and what was moved to trash",
    ),
    ToolSpec(
        name="kilo_git_status",
        summary="Read a workspace's repository status",
        resolve=_resolve_git_status, execute=_execute_git_status,
        returns="Branch, and the staged/unstaged/untracked paths",
    ),
    ToolSpec(
        name="kilo_install_package",
        summary="Install a Python package into a workspace",
        resolve=_resolve_install, execute=_execute_install,
        returns="pip's output. Asks at every trust level.",
    ),
)
