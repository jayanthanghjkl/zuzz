"""Face API — browser navigation routes.

Opening and closing the browser, going somewhere, and managing tabs. The policy behind
every one of them lives in :mod:`face.api.browser_support` and, behind that, in
:mod:`body.legs`; what is here is only the HTTP surface.

Three of the four groups in this package are split across this file and its two
siblings, and the split is by user intent rather than by line count: *where am I and
where do I go* (here), *what do I do there* (:mod:`face.api.routes_browser_interact`),
and *what does it cost me* (:mod:`face.api.routes_browser_manage`).

``session-status`` is a gauge and is not gated — a gauge behind a confirmation is a gauge
nobody looked at — and it lives in the sibling rather than here because it reports caps
and policy, which is what the confirmation is about.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from face.api.browser_support import (
    BrowserRequest,
    NavigateRequest,
    TabRequest,
    bind,
    gated,
    legs,
    preview,
    run,
)

router = APIRouter()


# -- lifecycle ---------------------------------------------------------


@router.post("/api/browser/open")
async def open_browser(request: Request, body: BrowserRequest) -> Dict[str, Any]:
    """Launch a browser session. Visible by default, not headless.

    Args:
        request: The incoming request.
        body: The headless flag and confirmation evidence.

    Returns:
        The session id, the headless state the browser reported about itself, and its
        own user agent.

    Raises:
        HTTPException: 503 without Playwright or Chromium, 409 while stopped, 403 when
            the gate refuses or headless is disallowed.
    """
    return await gated(request, "launch", body, legs(request), "open",
                       headless=body.headless, detail="launch a visible browser window",
                       preview_fields={"headless": body.headless})


@router.post("/api/browser/close")
async def close_browser(request: Request, body: BrowserRequest) -> Dict[str, Any]:
    """Close a browser session and everything in it.

    Closing a session that is already closed is a success rather than a 404: the browser
    is gone, which is what the caller asked for, and a second press of the Stop button
    should not read as an error.

    Args:
        request: The incoming request.
        body: The session and confirmation evidence.

    Returns:
        Whether a session was closed.

    Raises:
        HTTPException: 409 while stopped, 403 when the gate refuses.
    """
    stack = legs(request)
    live = [item["session_id"] for item in stack.controller.list_sessions()]
    if not (body.session_id in live or (not body.session_id and len(live) == 1)):
        return {
            "status": "ok",
            "result": {"closed": False, "session_id": body.session_id,
                       "reason": "that session was not open"},
            "decision": {"decision": "auto", "allowed": True, "reason": "nothing to close"},
        }

    async def execute(decision: PermissionDecision) -> Dict[str, Any]:
        """Close the session the gate approved.

        Args:
            decision: The allowed decision.

        Returns:
            Whether a session was closed, and which one.
        """
        closed = await stack.close(decision, body.session_id)
        return {
            "closed": bool(closed),
            "session_id": body.session_id,
        }

    return await run(
        request, "launch", body, execute,
        lambda: preview("close", session_id=body.session_id,
                        detail="shut the browser down and drop its tabs"),
        session_id=body.session_id,
    )


@router.post("/api/browser/navigate")
async def navigate(request: Request, body: NavigateRequest) -> Dict[str, Any]:
    """Go to a URL, after the allowlist check.

    Returns 202 twice over: when the gate asks, and when the allowlist asks about a
    reachable domain it has not seen. ``needs_domain_confirmation`` in the payload tells
    the page which one it is, so it can ask "add this site?" rather than showing a
    generic permission question.

    Args:
        request: The incoming request.
        body: The URL, the session and confirmation evidence.

    Returns:
        The navigation record, or a confirmation prompt.

    Raises:
        HTTPException: 403 for a never-navigable URL, 400 for a malformed one, 404 for
            a dead session, 502 for a page that would not load.
    """
    return await run(
        request, "navigate", body,
        bind(legs(request), "navigate", body.session_id, body.url, body.wait_until,
             explicit_user_command=body.explicit_user_command),
        lambda: preview("navigate", body.url, body.session_id),
        url=body.url, session_id=body.session_id,
    )


# -- tabs --------------------------------------------------------------


@router.get("/api/browser/tabs")
async def tabs(request: Request, session_id: str = "") -> Dict[str, Any]:
    """List the open tabs. Not gated; it names pages, it does not read them.

    Args:
        request: The incoming request.
        session_id: One session, or empty for all of them.

    Returns:
        The tab records, the hard cap and the soft-warning point.
    """
    return legs(request).list_tabs(session_id)


@router.post("/api/browser/new-tab")
async def new_tab(request: Request, body: TabRequest) -> Dict[str, Any]:
    """Open another tab.

    Not in the operation list this package was built against, and included anyway:
    without it a session has exactly one tab forever, which makes switch-tab and
    close-tab operations on a browser that can never have a second tab.

    The cap is enforced inside the Body, and the 409 it raises names the override
    that *would* work. Taking that override needs its own confirmed decision rather
    than the one that opened the sixth tab, so ``override`` is a separate request
    rather than a flag on this one — a tab cap that a single click could raise is a
    suggestion, and a runaway loop is the case it exists for.

    Args:
        request: The incoming request.
        body: The session, the URL for the tab, and confirmation evidence.

    Returns:
        The tab record and the cap fields.

    Raises:
        HTTPException: 409 at the cap, 404 for a dead session, 403 when refused.
    """
    return await run(
        request, "tabs", body,
        bind(legs(request), "open_tab", body.session_id, body.url or "about:blank"),
        lambda: preview("tabs", session_id=body.session_id, detail="open another tab",
                        url=body.url),
        session_id=body.session_id,
    )


@router.post("/api/browser/switch-tab")
async def switch_tab(request: Request, body: TabRequest) -> Dict[str, Any]:
    """Make a tab the active one.

    Args:
        request: The incoming request.
        body: The session and the tab id.

    Returns:
        The tab record.

    Raises:
        HTTPException: 404 for an unknown tab or session, 403 when refused.
    """
    return await gated(request, "tabs", body, legs(request), "switch_tab",
                       body.session_id, body.tab_id,
                       detail=f"focus tab {body.tab_id or 'the first one'}")


@router.post("/api/browser/close-tab")
async def close_tab(request: Request, body: TabRequest) -> Dict[str, Any]:
    """Close one tab.

    The session's only tab is not closed here — that goes with the browser itself,
    through ``/api/browser/close``, which is separately confirmed. A browser with no
    tabs cannot show the user what it is doing, and being able to see it is the reason
    the browser is visible at all.

    Args:
        request: The incoming request.
        body: The session and the tab id.

    Returns:
        What was closed and what remains.

    Raises:
        HTTPException: 409 for the last tab or a tab cap, 404 for an unknown tab.
    """
    return await gated(request, "tabs", body, legs(request), "close_tab",
                       body.session_id, body.tab_id,
                       detail=f"close tab {body.tab_id or 'the first one'}")