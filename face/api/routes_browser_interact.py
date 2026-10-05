"""Face API — browser interaction routes.

Everything that touches the contents of the page: clicking, filling, reading something
out of it, and searching. Navigation itself is in :mod:`face.api.routes_browser_nav`;
the caps, the quarantine and the allowlist are in
:mod:`face.api.routes_browser_manage`.

``search`` and ``extract`` sit here rather than with navigation because both read the
result. A search is a navigation the Body performs and ZUZZ then reads, and a search that
could not report what it found would not be worth confirming.

Only ``click`` takes a vision coordinate. The confidence and description ride along
with it so the gate scores the *action* rather than a bare point: a coordinate ZUZZ
found by looking at the screen and one a caller typed are not the same kind of click, and
the difference is exactly what the Amygdala scores.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from body.legs.exceptions import UrlRefused

from face.api.browser_support import (
    ClickRequest,
    ExtractRequest,
    FormRequest,
    SearchRequest,
    bind,
    gated,
    legs,
    preview,
    run,
)

router = APIRouter()


# -- acting on the page ------------------------------------------------


@router.post("/api/browser/click")
async def click(request: Request, body: ClickRequest) -> Dict[str, Any]:
    """Click an element, by selector, by text or by a vision coordinate.

    Args:
        request: The incoming request.
        body: The selector, or the coordinate and its confidence.

    Returns:
        The click record.

    Raises:
        HTTPException: 400 when neither a selector nor a coordinate was given, 404 when
            nothing matched, 403 for an off-page coordinate or a refused element.
    """
    coordinate = (body.x, body.y) if body.x is not None and body.y is not None else None
    if not body.selector and coordinate is None:
        raise HTTPException(
            status_code=400,
            detail="give a selector, the visible text of an element, or x and y",
        )
    return await gated(
        request, "click", body, legs(request), "click_element",
        body.session_id, body.selector, coordinates=coordinate,
        confidence=body.confidence, description=body.description,
        detail=body.description or body.selector or f"click at ({body.x}, {body.y})",
        preview_fields={"coordinates": list(coordinate) if coordinate else None},
    )


@router.post("/api/browser/fill-form")
async def fill_form(request: Request, body: FormRequest) -> Dict[str, Any]:
    """Fill form fields. Does not submit, unless asked.

    Args:
        request: The incoming request.
        body: ``{field handle: value}``, the submit flag and confirmation evidence.

    Returns:
        The fill record: what landed, what did not, and whether it submitted.

    Raises:
        HTTPException: 400 for an empty form body, 403 for a refused element.
    """
    stack = legs(request)
    if not body.form_data:
        raise HTTPException(
            status_code=400, detail='form_data is empty; send {"field": "value"}'
        )

    async def execute(decision: Any) -> Dict[str, Any]:
        """Fill — and optionally submit — the form the gate approved.

        Submitting reuses the *same* decision rather than a second one, because the
        confirmation popup said which button was going to be pressed and both acts were
        shown together in one prompt. A second decision here would mean a second popup
        for something the user was already told about.

        Args:
            decision: The allowed decision.

        Returns:
            The fill record, with the submit outcome folded in when one was asked for.
        """
        record = await stack.fill_form(
            decision, body.session_id, body.form_data, confidence=body.confidence
        )
        if body.submit:
            record["submit"] = await stack.forms.submit(
                decision, body.session_id, confirm_text=body.form_data.get("_submit", "")
            )
            record["submitted"] = True
        return record

    return await run(
        request, "fill", body, execute,
        lambda: preview("fill", session_id=body.session_id,
                        detail=f"fill {len(body.form_data)} field(s)"
                        + (" and submit" if body.submit else ", without submitting"),
                        fields=sorted(body.form_data), submits=bool(body.submit)),
        session_id=body.session_id,
    )


@router.post("/api/browser/extract")
async def extract(request: Request, body: ExtractRequest) -> Dict[str, Any]:
    """Read something out of the current page.

    Args:
        request: The incoming request.
        body: The mode, the selector, the cap override and confirmation evidence.

    Returns:
        The extraction record.

    Raises:
        HTTPException: 400 for an unknown mode, 404 for a selector that matched nothing,
            403 when the gate refuses.
    """
    return await gated(request, "extract", body, legs(request), "extract",
                       body.session_id, body.mode, body.selector, body.limit,
                       detail=f"read {body.mode} from the current page")


# -- search ------------------------------------------------------------


@router.post("/api/browser/search")
async def search(request: Request, body: SearchRequest) -> Dict[str, Any]:
    """Search the web.

    The engine's own domain is checked inside the call, so an engine that is not on the
    allowlist produces the same prompt as a site that is not — rather than a second,
    differently-shaped question.

    Args:
        request: The incoming request.
        body: The query, the engine, the result count and confirmation evidence.

    Returns:
        The search record, results included.

    Raises:
        HTTPException: 400 for an unknown engine or an empty query, 403 when refused.
    """
    stack = legs(request)
    if not str(body.query).strip():
        raise HTTPException(status_code=400, detail="no search query was given")
    try:
        action = stack.propose_search(body.query, body.engine, body.count)
    except (ValueError, UrlRefused) as exc:
        # An unknown engine is the caller's input being wrong, and it is raised from
        # the Body as a URL-shaped refusal because the engine's own domain is what
        # gets checked. Both are 400s, and letting the second escape the route would
        # turn a bad engine name into a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await run(
        request, "search", body,
        bind(stack, "search_web", body.session_id, body.query, body.engine, body.count),
        lambda: preview("search", action.target, body.session_id,
                        detail=f"search {body.engine} for a phrase", engine=body.engine),
        action=action, url=action.target, session_id=body.session_id,
        extra={"engine": body.engine},
    )