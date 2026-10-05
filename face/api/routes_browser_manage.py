"""Face API — browser session, capture and download routes.

The parts of browsing that cost something beyond a page load: what ZUZZ may do here
(``session-status``), a frame of the page (``screenshot``), and a file arriving from
somewhere else (``download``, ``downloads``). Plus the two allowlist edits, which are
the *user's* answer to a question ZUZZ asked rather than the agent widening its own
permissions.

The allowlist endpoints are **not** gated, and that is the one apparent exception in the
whole package. They record what the user said in the 202 the allowlist produced, so the
audit record stands in for the gate — see :func:`face.api.browser_support.allow` for why
that is the honest arrangement. ``/api/browser/forget-domain`` is how the user takes the
answer back.

``screenshot`` is categorised ``visual_capture`` rather than ``browse_internet`` on
purpose: a browser window routinely holds a logged-in banking session, and the pixels
are not a file the Body's secret-file guards can classify.

``download`` is the only route in this package that needs **two** decisions —
``download_files`` for the fetch and ``write_files`` for the write. One 202 answers both,
and ``write_action`` in the payload says what the second one is about, because a preview
showing only the URL would not tell the user a file is about to be written to disk.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from face.api.browser_support import (
    AllowlistRequest,
    DownloadRequest,
    ScreenshotRequest,
    allow,
    download_actions,
    download_preview,
    forget,
    gate,
    legs,
    preview,
    run,
)

router = APIRouter()


# -- session -----------------------------------------------------------


@router.get("/api/browser/session-status")
async def session_status(request: Request) -> Dict[str, Any]:
    """Return sessions, caps and policy. Not gated; a gauge, not an action.

    Reads only ZUZZ's own state: live sessions, tab limits, the download policy,
    persisted session domains, the search engines and the allowlist. Never a page's
    contents, never a cookie value.

    Args:
        request: The incoming request.

    Returns:
        The status record.

    Raises:
        HTTPException: 503 when the browser stack is not wired.
    """
    return legs(request).status()


@router.post("/api/browser/screenshot")
async def screenshot(request: Request, body: ScreenshotRequest) -> Any:
    """Return a PNG of the page.

    Categorised ``visual_capture`` rather than ``browse_internet``: a browser window
    routinely holds a logged-in banking session or a password manager, and the pixels
    are not a file the Body's secret-file guards can classify.

    Args:
        request: The incoming request.
        body: The session, the full-page flag and confirmation evidence.

    Returns:
        The PNG, or the 202 prompt as JSON — as JSON rather than as an empty image,
        because a route that caught the prompt and served a 1x1 PNG would hand out a
        frame the user never approved.

    Raises:
        HTTPException: 404 for a dead session, 409 while stopped.
    """
    stack = legs(request)
    store = None
    if body.store:
        lobe = getattr(request.app.state, "occipital_lobe", None)
        # ``engine``, not ``capture``: on the Occipital Lobe ``capture`` is the public
        # method that takes a decision, and the store hangs off the screenshot engine.
        store = getattr(getattr(lobe, "engine", None), "store", None)

    async def execute(decision: PermissionDecision) -> Dict[str, Any]:
        """Take the screenshot the gate approved.

        Args:
            decision: The allowed decision.

        Returns:
            The PNG bytes and whether the frame was also filed.
        """
        image = await stack.screenshot(
            decision, body.session_id, body.full_page, body.label, store
        )
        return {"image": image, "stored": store is not None, "bytes": len(image)}

    answer = await run(
        request, "screenshot", body, execute,
        lambda: preview("screenshot", session_id=body.session_id,
                        detail="capture the browser page as an image",
                        full_page=body.full_page),
        session_id=body.session_id,
    )
    if isinstance(answer, JSONResponse):
        return answer
    result = answer["result"]
    return Response(content=result["image"], media_type="image/png",
                    headers={"X-Zuzz-Bytes": str(result["bytes"]),
                             "X-Zuzz-Stored": "1" if result["stored"] else "0"})


# -- downloads ---------------------------------------------------------


@router.post("/api/browser/download")
async def download(request: Request, body: DownloadRequest) -> Dict[str, Any]:
    """Download a file into quarantine, through Body/Fingers.

    Two decisions: ``download_files`` for the fetch and ``write_files`` for the write.
    One 202 answers both, and ``write_action`` in the payload says what the second one
    is about — a preview showing only the URL would not tell the user that a file is
    about to be written to disk.

    Args:
        request: The incoming request.
        body: The URL, the elevated flag and confirmation evidence.

    Returns:
        The download record, including the ``sha256`` of what was written and the
        classification it was written under.

    Raises:
        HTTPException: 403 for an unrequested executable or a refused write, 413 over
            the size cap, 502 when the fetch failed.
    """
    stack = legs(request)
    _fetch, write_action, plan = download_actions(stack, body.url, body.elevated)

    async def execute(decision: PermissionDecision) -> Dict[str, Any]:
        """Fetch the file the gate approved and write it through Fingers.

        Args:
            decision: The allowed ``download_files`` decision.

        Returns:
            The download record.

        Raises:
            HTTPException: 403 when the second decision refuses the write. The user
                already answered one prompt, so this is a refusal rather than a second
                question — and it names the path that was refused.
        """
        _threat, write_decision = gate(request, write_action)
        if not write_decision.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"writing to {plan['destination']} is refused: "
                       f"{write_decision.reason}",
            )
        return await stack.download_file(
            decision, write_decision, body.session_id, body.url, body.elevated
        )

    return await run(request, "download", body, execute, lambda: download_preview(plan),
                     url=body.url, session_id=body.session_id,
                     extra={"plan": plan,
                            "write_action": {"category": str(write_action.category),
                                             "target": write_action.target}})


@router.get("/api/browser/downloads")
async def quarantine(request: Request) -> Dict[str, Any]:
    """List what is in the holding pen. Not gated; a gauge.

    Names files and sizes them. Reading one is a separate act with its own decision and
    moving one out is a third, which is the arrangement ``downloads`` is for.

    Args:
        request: The incoming request.

    Returns:
        The held files and the quarantine path.
    """
    stack = legs(request)
    entries = stack.list_downloads()
    return {"count": len(entries), "downloads": entries,
            "quarantine_dir": str(stack.downloads.root)}


# -- the allowlist -----------------------------------------------------


@router.post("/api/browser/allow-domain")
async def allow_domain(request: Request, body: AllowlistRequest) -> Dict[str, Any]:
    """Add a domain the user just approved. Not gated; audited instead.

    This is the user answering the 202 the allowlist produced, not the agent widening
    its own permissions, and :func:`face.api.browser_support.allow` explains why the
    audit record stands in for the gate here. The user can take it back with
    ``/api/browser/forget-domain``.

    Args:
        request: The incoming request.
        body: The domain, its category, and whether to keep or persist it.

    Returns:
        What changed.

    Raises:
        HTTPException: 403 for a domain that is never navigable.
    """
    return {"status": "ok", "result": allow(request, legs(request), body.domain,
                                            body.category, body.remember, body.persist)}


@router.post("/api/browser/forget-domain")
async def forget_domain(request: Request, body: AllowlistRequest) -> Dict[str, Any]:
    """Forget a domain: remove the approval and delete any state saved for it.

    The revocation the "Forget this domain" button calls. Both halves matter, and doing
    only one is how a forget leaves a working login behind: the allowlist entry goes so
    the domain asks again, and the encrypted state file goes so a later approval does
    not silently restore the session the user discarded.

    Args:
        request: The incoming request.
        body: The domain.

    Returns:
        What changed, and the domains still persisted.
    """
    return {"status": "ok", "result": forget(request, legs(request), body.domain)}