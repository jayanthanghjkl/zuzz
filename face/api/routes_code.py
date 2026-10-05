"""Face API — the coding routes.

Eleven endpoints over :mod:`body.mouth.kilo`, and the split between them is the
design rather than a convenience:

* **Five gated, generating operations** — ``generate``, ``review``, ``debug``,
  ``refactor``, ``test``. None of them writes. Each returns a proposal with a
  ``diff_id``, and each needs a separate call under ``modify_project`` before a byte
  moves. The Architect runs before the Coder inside ``generate`` because a Coder given
  only "build me a Flask API" invents a file layout; given one, it fills it in, and
  the design is returned beside the diff so the user can see *why* the files are
  shaped as they are.
* **Two operations that act rather than propose** — ``apply`` and ``rollback``, each
  needing two decisions, and both answering one 202 with both.
* **Two that run something** — ``run`` (pytest) and ``install``. ``install`` has no
  route of its own on purpose: it asks at **every** trust level including 5
  (immutable rule R7), so it belongs to the tool catalogue and the chat, where the
  prompt is already in front of the user, rather than to a page that could turn it
  into one button.
* **Two gauges** — ``agents-status`` and ``workspace``. Neither asks for anything.
  They are what the user reads *in order to decide*, and a gauge behind a
  confirmation is a gauge nobody looked at.
* **``diff``** is the preview and the reject button in one call. Rejecting needs no
  permission: a user who says no has already exercised the authority that matters,
  and it is the only outcome of the whole flow that is not a permission decision.

``review`` is filed as ``read_files`` and not as ``generate_code``, because reading a
file and reviewing a file are the same exposure. ``test`` and ``run`` are likewise
separate rows — proposing tests is ``generate_code`` and running them is
``execute_code`` — and the gap between them is the whole reason ``execute_code``
exists: a test file an agent wrote a moment ago is not a gentler thing to run than
one the user has been running all week.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from face.api.code_support import (
    CodeRequest,
    engine,
    run,
    write_action,
    write_decision,
)

router = APIRouter()


class GenerateRequest(CodeRequest):
    """Build something, or only design it.

    Attributes:
        description: What to build, in the user's words.
        agent: Which writer implements it. The Architect always designs first.
        plan_only: Return the design without asking an agent to write anything.
    """

    description: str = Field("", description="What to build, in your words")
    agent: str = Field("coder", description="Which writing agent implements it")
    plan_only: bool = Field(False, description="Design it, do not write the files yet")


class ReviewRequest(CodeRequest):
    """Review one file.

    Attributes:
        path: Workspace-relative path.
    """

    path: str = Field("", description="Workspace-relative path to review")


class DebugRequest(CodeRequest):
    """Explain a failure, and propose a fix where one can be proposed.

    Attributes:
        error: The traceback or the message.
        code: The offending source, when the caller has it.
    """

    error: str = Field("", description="The traceback or error message")
    code: str = Field("", description="The offending source, if you have it")


class RefactorRequest(CodeRequest):
    """Rewrite one file towards a goal.

    Attributes:
        path: Workspace-relative path.
        goal: What "better" means here.
    """

    path: str = Field("", description="Workspace-relative path to refactor")
    goal: str = Field("", description="What better means for this file")


class TestRequest(CodeRequest):
    """Have the Tester write tests for a file.

    Attributes:
        path: Workspace-relative path to cover.
        goal: What to cover. Blank means behaviour and edges.
    """

    path: str = Field("", description="Workspace-relative path to write tests for")
    goal: str = Field("", description="What to cover, e.g. the error paths")


class RunRequest(CodeRequest):
    """Run a test suite.

    Attributes:
        path: The test path or pattern handed to pytest.
    """

    path: str = Field("tests", description="Test path or pattern for pytest")


class DiffRequest(CodeRequest):
    """Preview a proposal, or discard one.

    ``reject`` is here rather than on a path of its own so that the Code page has one
    place to talk to about proposals. Discarding is not gated and never will be: the
    user saying no is the system working.

    Attributes:
        diff_id: One proposal, or blank for all of them.
        reject: Discard it instead of showing it.
    """

    diff_id: str = Field("", description="One proposal, or blank for every pending one")
    reject: bool = Field(False, description="Discard the proposal instead of showing it")


class ChangeRequest(CodeRequest):
    """Apply or undo a proposal. Two decisions either way.

    Attributes:
        diff_id: The proposal.
        force: Overwrite a file that changed since the preview. False by default,
            because overwriting a file the user edited since they looked at the diff is
            the one way a diff viewer causes data loss.
    """

    diff_id: str = Field("", description="The proposal to act on")
    force: bool = Field(False, description="Overwrite a file that changed since the preview")


def _estimate(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Return the file count and size a confirmation popup shows.

    Args:
        proposal: A proposal record from ``to_dict``.

    Returns:
        The keys :func:`face.api.file_confirmations.confirmation_payload` reads. Total
        size is the ``after`` text, not the ``before``: the question is how many bytes
        are about to land, and a preview showing the size of what is being replaced
        would answer a question nobody asked.
    """
    changes = proposal.get("changes") or []
    return {
        "file_count": len(changes),
        "total_bytes": sum(len(str(item.get("after") or "")) for item in changes),
        "total_human": f"{len(changes)} file(s)",
        "reversible": True,
    }


# -- generating --------------------------------------------------------


@router.post("/api/code/generate")
async def generate(request: Request, body: GenerateRequest) -> Dict[str, Any]:
    """Design it, then write it as a proposal. Nothing is written to disk.

    Args:
        request: The incoming request.
        body: The description, the workspace and the confirmation evidence.

    Returns:
        The proposal with its ``diff_id``, plus the design that shaped it.

    Raises:
        HTTPException: 400 for an empty description, 403 for a refusal, 404 for a
            workspace that is not there, 429 when neither model answered.
    """
    stack = engine(request)
    description = body.description.strip()
    if not description:
        raise ValueError("say what to build")
    contents = lambda: {  # noqa: E731 — a one-line preview, not worth a def
        "description": description[:500],
        "workspace": body.workspace or "the scratch workspace",
        "note": "proposes a diff; it writes nothing until you apply it",
    }
    return await run(
        request, "generate", body,
        lambda decision: stack.generate(
            decision, description, body.workspace,
            agent="architect" if body.plan_only else body.agent,
        ),
        contents,
    )


@router.post("/api/code/review")
async def review(request: Request, body: ReviewRequest) -> Dict[str, Any]:
    """Review a file for bugs, security problems and dead code. Writes nothing.

    Args:
        request: The incoming request.
        body: The path, the workspace and the confirmation evidence.

    Returns:
        Findings with ``file:line``, most severe first.

    Raises:
        HTTPException: 400 for a blank path, 403 for a refusal.
    """
    stack = engine(request)
    path = body.path.strip()
    if not path:
        raise ValueError("name the file to review")
    return await run(
        request, "review", body,
        lambda decision: stack.review(decision, path, body.workspace),
        lambda: {"description": f"Review {path}",
                  "workspace": body.workspace or "the scratch workspace"},
    )


@router.post("/api/code/debug")
async def debug(request: Request, body: DebugRequest) -> Dict[str, Any]:
    """Find the root cause, and propose the fix where one can be proposed.

    A Debugger that explains without producing a file is a normal outcome, so the
    response carries ``proposed: 0`` rather than an error in that case.

    Args:
        request: The incoming request.
        body: The error, the optional source, the workspace and the evidence.

    Returns:
        The explanation, and a ``diff_id`` when a fix could be proposed.

    Raises:
        HTTPException: 400 for an empty error, 403 for a refusal.
    """
    stack = engine(request)
    error = body.error.strip()
    if not error:
        raise ValueError("paste the error you want explained")
    return await run(
        request, "debug", body,
        lambda decision: stack.debug(decision, error, body.code, body.workspace),
        lambda: {"description": "Find the cause of an error and propose a fix",
                  "workspace": body.workspace or "the scratch workspace",
                  "note": "proposes a diff; it writes nothing until you apply it"},
    )


@router.post("/api/code/refactor")
async def refactor(request: Request, body: RefactorRequest) -> Dict[str, Any]:
    """Rewrite one file towards a stated goal, as a proposal.

    Args:
        request: The incoming request.
        body: The path, the goal, the workspace and the evidence.

    Returns:
        The proposal and the file's current contents, so the diff has a readable
        "before" beside it.

    Raises:
        HTTPException: 400 for a blank path or goal, 403 for a refusal.
    """
    stack = engine(request)
    path, goal = body.path.strip(), body.goal.strip()
    if not path or not goal:
        raise ValueError("name the file and what better means for it")
    return await run(
        request, "refactor", body,
        lambda decision: stack.refactor(decision, path, goal, body.workspace),
        lambda: {"description": f"Refactor {path}", "goal": goal[:500],
                  "workspace": body.workspace or "the scratch workspace",
                  "note": "proposes a diff; it writes nothing until you apply it"},
    )


@router.post("/api/code/test")
async def write_tests(request: Request, body: TestRequest) -> Dict[str, Any]:
    """Have the Tester write tests for a file, as a proposal. Nothing is run.

    Writing tests and running them are separate calls under separate rows, and this is
    the writing one. The next call is ``/api/code/run``.

    Args:
        request: The incoming request.
        body: The path, the goal, the workspace and the evidence.

    Returns:
        The proposal, and where the tests were proposed to land.

    Raises:
        HTTPException: 400 for a blank path, 403 for a refusal.
    """
    stack = engine(request)
    path = body.path.strip()
    if not path:
        raise ValueError("name the file to write tests for")
    return await run(
        request, "generate", body,
        lambda decision: stack.write_tests(decision, path, body.goal, body.workspace),
        lambda: {"description": f"Write tests for {path}", "goal": body.goal[:500],
                  "workspace": body.workspace or "the scratch workspace",
                  "note": "proposes a diff; it writes nothing until you apply it"},
    )


# -- proposals ---------------------------------------------------------


@router.post("/api/code/diff")
async def diff(request: Request, body: DiffRequest) -> Dict[str, Any]:
    """Show a proposal, or discard it. Neither asks for permission.

    Args:
        request: The incoming request.
        body: The proposal id, and whether to reject it.

    Returns:
        The pending proposals with their text, or the result of discarding one.

    Raises:
        HTTPException: 404 for an id that is not pending. Proposals live in memory
            only, so a stale id after a restart is a real answer: nothing is waiting.
    """
    stack = engine(request)
    if body.reject:
        return {"status": "ok", "result": stack.reject(body.diff_id.strip())}
    return {"status": "ok", "result": stack.preview_diff(body.diff_id.strip())}


# -- writing -----------------------------------------------------------


@router.post("/api/code/apply")
async def apply(request: Request, body: ChangeRequest) -> Dict[str, Any]:
    """Write an approved proposal through Body/Fingers.

    Two decisions. ``modify_project`` says this project may be rewritten;
    ``write_files`` names the root the bytes land in. One 202 answers both, and
    ``write_action`` in the payload says what the second one is about — a popup
    showing only a proposal id would not tell the user that files are about to be
    overwritten.

    Args:
        request: The incoming request.
        body: The proposal id, the workspace, the force flag and the evidence.

    Returns:
        What was written and when.

    Raises:
        HTTPException: 404 for an unknown proposal, 409 for one already applied or a
            file that moved, 403 for a refusal.
    """
    stack = engine(request)
    if not body.diff_id.strip():
        raise ValueError("name the proposal to apply")
    pending = stack.preview_diff(body.diff_id.strip())
    root = str(pending.get("workspace") or stack.workspace.resolve(body.workspace).root)
    # The target is the workspace root rather than the proposal id, so the parked
    # confirmation cannot be spent on a different proposal in the same folder, and so
    # one answer covers every file the change set touches.
    action = stack.propose("apply", root)
    return await run(
        request, "apply", body,
        lambda decision: stack.apply(
            decision, write_decision(request, root, "apply"),
            body.diff_id.strip(), force=body.force,
        ),
        lambda: _estimate(pending),
        action=action,
        extra={
            "write_action": {"category": str(write_action(root).category),
                             "target": root},
            "workspace": root, "diff_id": body.diff_id.strip(),
        },
    )


@router.post("/api/code/rollback")
async def rollback(request: Request, body: ChangeRequest) -> Dict[str, Any]:
    """Undo an applied proposal through Body/Fingers.

    Two decisions again, and the same shape. A rollback is not a softer apply: it
    overwrites the files the apply wrote, so a user who edited one of them afterwards
    loses that edit — which is why the restore is recorded per file rather than
    summarised, and why ``force`` exists here too.

    Args:
        request: The incoming request.
        body: The proposal id, the workspace and the evidence.

    Returns:
        What was restored and what was trashed.

    Raises:
        HTTPException: 404 for a proposal that was never applied, 409 for one that was,
            403 for a refusal.
    """
    stack = engine(request)
    if not body.diff_id.strip():
        raise ValueError("name the proposal to roll back")
    pending = stack.preview_diff(body.diff_id.strip())
    root = str(pending.get("workspace") or stack.workspace.resolve(body.workspace).root)
    action = stack.propose("rollback", root)
    return await run(
        request, "rollback", body,
        lambda decision: stack.rollback(
            decision, write_decision(request, root, "rollback"), body.diff_id.strip()
        ),
        lambda: _estimate(pending),
        action=action,
        extra={
            "write_action": {"category": str(write_action(root, "rollback").category),
                             "target": root},
            "workspace": root, "diff_id": body.diff_id.strip(),
        },
    )


# -- running -----------------------------------------------------------


@router.post("/api/code/run")
async def run_tests(request: Request, body: RunRequest) -> Dict[str, Any]:
    """Run pytest in a workspace. An argument vector, never a shell.

    Args:
        request: The incoming request.
        body: The test path, the workspace and the evidence.

    Returns:
        Stdout and stderr separately, the exit code, and the per-test outcomes.

    Raises:
        HTTPException: 403 for a command outside the allowlist, 409 for one that ran
            out of time.
    """
    stack = engine(request)
    return await run(
        request, "tests", body,
        lambda decision: stack.run_tests(decision, body.workspace, body.path),
        lambda: {"description": f"Run pytest on {body.path}",
                 "workspace": body.workspace or "the scratch workspace",
                 "note": "runs a test suite; it writes nothing"},
    )


# -- gauges ------------------------------------------------------------


@router.get("/api/code/workspace")
async def workspace(request: Request, path: str = "") -> Dict[str, Any]:
    """Return one workspace's record, file tree and git status. Not gated.

    Args:
        request: The incoming request.
        path: A workspace name or path. Blank is the scratch root.

    Returns:
        The workspace, its files, and its repository's status.

    Raises:
        HTTPException: 404 when there is no repository, which is a normal state for a
            scratch workspace rather than a failure of the page.
    """
    stack = engine(request)
    resolved = stack.workspace.resolve(path)
    record: Dict[str, Any] = {
        "workspace": resolved.to_dict(), "tree": stack.workspace_tree(path),
    }
    try:
        record["git"] = stack.git_status(path)
    except Exception as exc:  # noqa: BLE001 — a missing repo is not a broken route
        record["git"] = {"error": str(exc), "repository": False}
    return record


@router.get("/api/code/agents-status")
async def agents_status(request: Request) -> Dict[str, Any]:
    """Return the agents, the routing and every policy the Code page shows.

    Not gated. This is the page's answer to "what can this thing actually do", and a
    user who has to approve being told that is a user who approves it without reading.

    Args:
        request: The incoming request.

    Returns:
        Everything :meth:`body.mouth.kilo.kilo_engine.KiloEngine.status` reports.
    """
    return {"status": "ok", "result": engine(request).status()}