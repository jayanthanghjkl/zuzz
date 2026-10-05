"""Face API — browser request models and the shared permission flow.

The fourteen browser endpoints share one flow, and it is the flow the file, desktop
and vision routes already use, deliberately: a user who has learned to read one set
of confirmation prompts can read the other.

1. Build the :class:`~brain.prefrontal_cortex.action.Action` through
   :meth:`~body.legs.browser_actions.BrowserActions.propose`, so the target the gate
   decides about is the exact string the Body will later insist on.
2. Have the Amygdala score it.
3. Have the gate decide at the current trust level.
4. ``blocked`` → 403 (409 while the stop is engaged). ``ask`` → 202 with a
   ``confirmation_id``. ``auto`` → execute and return 200.

A confirmation is answered by re-posting the same request with ``confirmed: true``
and the ``confirmation_id``. The parked action is re-decided at that moment, so a
stop that landed while the popup was open still blocks.

**The browser has one refusal the other routes do not, and it is not an error.**
:meth:`run` catches
:class:`~body.legs.exceptions.DomainConfirmationRequired` — an *unknown but
reachable* domain — and turns it into the same 202 prompt, with ``domain`` and
``url`` in the payload so the popup can say which site is being asked about. That is
the allowlist doing its job, and it is the single most common answer the browser
will give: reachable, but the user has not seen it before.

Two decisions for one download. ``download_files`` gates the fetch and
``write_files`` gates the write into quarantine, because they are two acts on two
things, and a confirmation for "fetch this" should not be spendable on "put it at
C:\\...". :func:`download` builds both and the route returns the second one as part
of the same 202, so the user answers one question and two decisions come out of it.

Status codes: 200 success, 202 awaiting confirmation, 400 bad request or a refused
URL, 403 blocked by rule or classification, 404 no such session, tab or element,
409 kill switch, tab cap or a state conflict, 413 download too large, 503 Playwright
or Chromium unavailable, 500 internal.

Kept separate from the route module so the route file holds endpoints and this file
holds the flow, which is the same split :mod:`face.api.vision_support` makes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain.amygdala.threat_detector import ThreatDetector
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_actions import BrowserActions
from body.legs.exceptions import (
    BlockedDomainRefused,
    BrowserUnavailable,
    DecisionRefused,
    DecisionReplayRefused,
    DomainConfirmationRequired,
    DownloadTooLarge,
    DryRunRefused,
    ElementNotFound,
    ElementRefused,
    ExecutableDownloadRefused,
    FormFillRefused,
    InvalidUrlRefused,
    KillSwitchRefused,
    LastTabRefused,
    NavigationFailed,
    PrivateNetworkRefused,
    QuarantineRefused,
    SchemeRefused,
    SessionPersistenceRefused,
    SessionRefused,
    TabNotFound,
    TooManyTabsRefused,
    UrlRefused,
)
from core.audit import (
    ALLOWLIST_CHANGED,
    BLOCKED,
    CONFIRMATION_REQUESTED,
    PRECHECK_REFUSAL,
    TAB_LIMIT_REFUSED,
    URL_BLOCKED,
)

from face.api.file_confirmations import confirmation_payload

#: Refusals that are the caller's input being wrong, not the system's.
BAD_INPUT = (InvalidUrlRefused, ElementRefused, FormFillRefused, ValueError,
             TypeError, KeyError)


class BrowserRequest(BaseModel):
    """Fields every browser request accepts."""

    session_id: str = Field("", description="Session to act on")
    headless: bool = Field(
        False, description="Run without a window; honoured only if allowed"
    )
    confirmed: bool = Field(False, description="The user approved the 202 prompt")
    confirmation_id: str = Field("", description="Id returned with the 202")
    dry_run: bool = Field(False, description="Describe the action, do not perform it")
    allow_domain: bool = Field(
        False, description="Answer a domain prompt by approving the domain itself"
    )
    remember_domain: bool = Field(
        True, description="Keep the domain approval; False approves it for this session"
    )
    persist_domain: bool = Field(
        False, description="Also keep cookies and local storage for the domain"
    )


class NavigateRequest(BrowserRequest):
    """A navigation."""

    url: str = Field(..., description="Where to go; checked against the allowlist")
    explicit_user_command: bool = Field(
        False, description="The user named this URL, so confirm even if listed"
    )
    wait_until: str = Field("domcontentloaded", description="Playwright readiness")


class ClickRequest(BrowserRequest):
    """A click, by selector, by text or by vision coordinate."""

    selector: str = Field("", description="A CSS selector or visible text")
    x: Optional[int] = Field(None, description="Vision-supplied x, in CSS pixels")
    y: Optional[int] = Field(None, description="Vision-supplied y, in CSS pixels")
    confidence: Optional[float] = Field(
        None, description="The Occipital Lobe's confidence for that coordinate"
    )
    description: str = Field("", description="What the caller meant to click")


class FormRequest(BrowserRequest):
    """A form fill."""

    form_data: Dict[str, str] = Field(
        default_factory=dict, description="{field handle: value}"
    )
    submit: bool = Field(False, description="Also press submit afterwards")
    confidence: Optional[float] = Field(
        None, description="Vision confidence for a field located by box"
    )


class ExtractRequest(BrowserRequest):
    """An extraction."""

    mode: str = Field("text", description="text, links, html, table, metadata, "
                                         "fields or images")
    selector: str = Field("", description="CSS selector, where the mode takes one")
    limit: int = Field(0, description="Override the configured cap for this call")


class SearchRequest(BrowserRequest):
    """A web search."""

    query: str = Field(..., description="What to search for")
    engine: str = Field("duckduckgo", description="duckduckgo, google or bing")
    count: int = Field(10, description="How many results, 1-50")


class DownloadRequest(BrowserRequest):
    """A download."""

    url: str = Field(..., description="What to download")
    elevated: bool = Field(
        False, description="The user specifically asked for an executable"
    )


class ScreenshotRequest(BrowserRequest):
    """A screenshot."""

    full_page: bool = Field(False, description="Whole scrollable page")
    label: str = Field("", description="Short name, when the frame is stored")
    store: bool = Field(False, description="Also file it in the screenshot store")


class TabRequest(BrowserRequest):
    """A tab operation."""

    tab_id: str = Field("", description="The tab; empty means the first one")
    url: str = Field("", description="A URL for a new tab")


class AllowlistRequest(BaseModel):
    """An allowlist edit."""

    domain: str = Field(..., description="The host")
    category: str = Field("other", description="A label for the UI")
    remember: bool = Field(True, description="Keep it, or approve for this session")
    persist: bool = Field(False, description="Keep cookies for this domain too")


def legs(request: Request) -> BrowserActions:
    """Return the browser stack wired into the app.

    Args:
        request: The incoming request.

    Returns:
        The :class:`~body.legs.browser_actions.BrowserActions`.

    Raises:
        HTTPException: 503 when it was never wired. A server with no browser stack
            cannot browse, and saying so plainly beats an AttributeError from deep
            inside a route.
    """
    stack = getattr(request.app.state, "legs", None)
    if stack is None:
        raise HTTPException(
            status_code=503, detail="the browser is not available on this server"
        )
    return stack


def gate(request: Request, action: Action) -> tuple:
    """Score and decide one browser action.

    Args:
        request: Used to reach the app's gate and trust level.
        action: The proposed action, built by ``BrowserActions.propose``.

    Returns:
        The Amygdala's assessment and the gate's verdict.
    """
    level = request.app.state.trust.current()
    threat = ThreatDetector().assess(action)
    decision = request.app.state.permission_gate.decide(action, level, threat)
    return threat, decision


def audit(
    request: Request,
    event: str,
    target: str,
    decision: Any = None,
    category: str = "",
    **fields: Any,
) -> None:
    """Record a safety event in the audit log.

    Args:
        request: Used to reach the audit log.
        event: The event type.
        target: What the action acted on.
        decision: The verdict, used for its reason and level.
        category: The action category, when it is not the target's own.
        **fields: Extra detail recorded verbatim.

    Returns:
        Nothing. An audit log that is not wired is not an error; the routes work
        without one and the events are lost rather than the request failing.
    """
    log = getattr(request.app.state, "audit", None)
    if log is None:
        return
    reason = getattr(decision, "reason", "") or ""
    level = getattr(decision, "decision", "") or ""
    log.record(
        event,
        reason,
        category=category or str(decision.action.category) if decision else category,
        target=target,
        level=level,
        rule_id=getattr(decision, "rule_id", "") or "",
        trust_level=getattr(decision, "trust_level", None),
        **fields,
    )


async def run(
    request: Request,
    operation: str,
    body: BrowserRequest,
    execute: Callable[[PermissionDecision], Any],
    preview: Callable[[], Dict[str, Any]],
    action: Optional[Action] = None,
    url: str = "",
    session_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate, confirm and run one browser operation.

    Args:
        request: Used to reach the app's collaborators.
        operation: The operation name, for the target string and the log.
        body: The request, including confirmation evidence.
        execute: The Body call, invoked only with an allowed decision.
        preview: What the confirmation popup should show.
        action: A pre-built action, for the two decisions that are built
            together — the download's ``write_files``.
        url: The URL involved, when there is one.
        session_id: The session, for the log.
        extra: Extra fields merged into a 202 payload.

    Returns:
        The result, a dry-run plan, or a confirmation prompt.

    Raises:
        HTTPException: Mapped from the Body's refusals.
    """
    stack = legs(request)
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    action = action if action is not None else stack.propose(operation, url)
    threat, decision = gate(request, action)
    if decision.blocked:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if body.dry_run and not action.dry_run:
        # A dry run is a description, not a lighter action. It is turned into a
        # ``dry_run`` action *before* the gate sees it, so the gate scores the thing
        # that would actually happen and a dry run can never be reported as the
        # performance of it — and so it reaches the Body, which refuses a dry-run
        # decision outright rather than navigating "a bit".
        action = replace(action, dry_run=True)
        threat, decision = gate(request, action)
    if decision.requires_confirmation and not body.confirmed:
        return _prompt(
            request, body, action, decision, threat, preview(), operation, extra
        )
    if decision.requires_confirmation:
        decision = await confirm(request, body, action)
    if action.dry_run:
        # Described, not performed, and the short circuit is here rather than after
        # ``execute`` because the Body refuses a dry-run decision outright — which is
        # the right behaviour for anything that calls the Body directly, and the
        # wrong behaviour for a caller who ticked "dry run only" and is owed an
        # answer. The plan is the preview plus what the gate and the Amygdala
        # concluded, so "show me what would happen" returns the same facts the
        # confirmation popup shows rather than a promise of them.
        #
        # Nothing is written to the audit log: a dry run changed nothing, and the
        # audit log is a record of things that happened.
        return {
            "status": "dry_run",
            "plan": {**preview(), "decision": decision.to_dict(),
                     "threat_score": threat.score},
            "decision": decision.to_dict(),
        }
    try:
        result = await execute(decision)
    except DomainConfirmationRequired as exc:
        # The allowlist's own answer: reachable, but the user has not seen this
        # domain. It is a question, and it becomes the same 202 every other
        # confirmation becomes — with the domain in it, so the popup can say which
        # site is being asked about rather than showing a raw URL.
        #
        # Answering it is one round trip, not two. The user ticks "remember this
        # site", re-posts, and the approval is recorded here and the operation is
        # retried immediately, rather than making the page call /allow-domain and
        # then re-post the original operation itself. Two round trips for one
        # question is how a caller gets it half-right, and the retry is bounded to
        # exactly once below, so an approval that somehow does not take effect
        # produces the prompt again rather than a loop.
        if body.allow_domain:
            allow(
                request, stack, exc.domain, category="other",
                remember=body.remember_domain, persist=body.persist_domain,
            )
            try:
                result = await execute(decision)
            except DomainConfirmationRequired:
                return _domain_prompt(request, body, action, decision, threat,
                                      preview, operation, extra, exc)
            except Exception as retry_exc:  # noqa: BLE001 — mapped like any refusal
                if isinstance(retry_exc, HTTPException):
                    raise
                _audit_refusal(request, operation, action, retry_exc, url)
                raise refuse(retry_exc) from retry_exc
        else:
            return _domain_prompt(request, body, action, decision, threat,
                                  preview, operation, extra, exc)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        _audit_refusal(request, operation, action, exc, url)
        raise refuse(exc) from exc
    request.app.state.logger.log(
        "face", "browser_api_ok", operation=operation, target=action.target,
        session=session_id, dry_run=bool(action.dry_run),
    )
    return {"status": "ok", "result": result, "decision": decision.to_dict()}


def _domain_prompt(
    request: Request,
    body: BrowserRequest,
    action: Action,
    decision: PermissionDecision,
    threat: Any,
    preview: Callable[[], Dict[str, Any]],
    operation: str,
    extra: Optional[Dict[str, Any]],
    exc: DomainConfirmationRequired,
) -> JSONResponse:
    """Turn an unknown-but-reachable domain into the 202 prompt.

    Split out of :func:`run` because the same prompt is needed twice there — once on
    the first sighting, and once more if the user's approval failed to take effect —
    and because it is the one answer in this flow that is not about the gate.

    Args:
        request: Used to park the action.
        body: The request, for its trust level.
        action: The action that was refused.
        decision: The decision to downgrade to an ask.
        threat: The Amygdala's assessment, kept so the parked entry can be re-scored.
        preview: The popup contents.
        operation: The operation name, for the log.
        extra: Extra fields merged into the payload.
        exc: The refusal, which carries the domain.

    Returns:
        The 202, asking about the domain.
    """
    return _prompt(
        request,
        body,
        action,
        _as_ask(decision, exc.reason),
        threat,
        preview(),
        operation,
        {"domain": exc.domain, "url": exc.url or getattr(body, "url", ""),
         "needs_domain_confirmation": True, **(extra or {})},
    )


async def confirm(
    request: Request, body: BrowserRequest, action: Action
) -> PermissionDecision:
    """Answer a parked confirmation.

    The parked entry is **popped**, not read, so a confirmation is single-use: a
    replay of the same id finds nothing and is refused. The action is re-decided
    here rather than upgraded blindly, so a stop that landed while the popup was
    open still blocks, and the parked action must be the action being requested — an
    approval for "go to github.com" cannot be spent on "go to a paste site".

    Args:
        request: Used to reach the app's collaborators.
        body: The request carrying the confirmation evidence.
        action: The action being requested now.

    Returns:
        An allowed decision.

    Raises:
        HTTPException: 403 when the id is unknown, spent, or for another action;
            409 while the stop is engaged; 403 or 409 when the re-decision no longer
            permits the action.
    """
    if not body.confirmation_id:
        raise HTTPException(
            status_code=403, detail="this action still needs confirmation"
        )
    if request.app.state.kill_switch.is_killed():
        raise HTTPException(
            status_code=409, detail="kill switch engaged; reset the stop to continue"
        )
    parked = request.app.state.confirmations.pop(body.confirmation_id)
    if parked is None or parked["action"].target != action.target:
        raise HTTPException(
            status_code=403,
            detail="that confirmation has expired or was for a different action",
        )
    threat, decision = gate(request, action)
    # Re-decided so that a *block* which landed while the popup was open still
    # blocks, and so the log records the level the answer was given at.
    #
    # A re-decision that still says ``ask`` is not a second question. The user has
    # answered this one, and the answer is what turns an ``ask`` into an ``allow`` —
    # which the Body needs, because the Body's invariant is that it only ever acts on
    # an *allowed* decision and refuses everything else. Without this, a level-1
    # browser could never be driven at all: every confirmation would re-ask, and the
    # user would be stuck at a prompt that never closes. ``blocked`` is different —
    # it means no trust level would permit this, so it refuses.
    if decision.blocked:
        audit(request, BLOCKED, action.target, decision)
        raise HTTPException(status_code=403, detail=decision.reason)
    if not decision.allowed:
        decision = replace(
            decision,
            allowed=True,
            requires_confirmation=False,
            decision="allow",
            reason=f"confirmed by the user; the gate had said: {decision.reason}",
        )
    return decision


def _prompt(
    request: Request,
    body: BrowserRequest,
    action: Action,
    decision: Any,
    threat: Any,
    preview: Dict[str, Any],
    operation: str,
    extra: Optional[Dict[str, Any]],
) -> JSONResponse:
    """Park an action and return the 202 prompt.

    Args:
        request: Used to reach the app's collaborators.
        body: The request, for the dry-run flag.
        action: The proposed action.
        decision: The ``ask`` verdict.
        threat: The Amygdala's assessment.
        preview: What the popup shows.
        operation: The operation name.
        extra: Extra fields merged into the payload.

    Returns:
        The 202 response.
    """
    confirmation_id = request.app.state.confirmations.create(
        action, decision, preview
    )
    audit(
        request,
        CONFIRMATION_REQUESTED,
        action.target,
        decision,
        operation=operation,
        domain=(extra or {}).get("domain", ""),
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "confirmation_required",
            **confirmation_payload(
                confirmation_id, operation, decision, threat, preview
            ),
            "action": {
                "category": str(action.category),
                "target": action.target,
                "operation": action.detail.get("operation", operation),
                "dry_run": action.dry_run,
            },
            **(extra or {}),
        },
    )


def _as_ask(decision: PermissionDecision, reason: str) -> PermissionDecision:
    """Return a copy of a decision that asks, for an allowlist question.

    Args:
        decision: The verdict the gate gave for this operation.
        reason: Why the allowlist wants to ask.

    Returns:
        The same decision with ``decision`` set to ``ask`` and the reason appended,
        so the confirmation popup shows both the gate's reason and the allowlist's.
    """
    from dataclasses import replace

    return replace(decision, decision="ask", requires_confirmation=True,
                   allowed=False, reason=f"{decision.reason}; {reason}")


def _audit_refusal(
    request: Request,
    operation: str,
    action: Action,
    exc: Exception,
    url: str,
) -> None:
    """Record a refusal the gate would not have produced, in the right event type.

    Args:
        request: Used to reach the audit log.
        operation: The operation name.
        action: The action being attempted.
        exc: The refusal.
        url: The URL involved, when there was one.

    Returns:
        Nothing.
    """
    if isinstance(exc, TooManyTabsRefused):
        event, target = TAB_LIMIT_REFUSED, action.target
    elif isinstance(exc, UrlRefused):
        event, target = URL_BLOCKED, url or action.target
    elif isinstance(exc, (KillSwitchRefused, DecisionRefused)):
        event, target = BLOCKED, action.target
    elif isinstance(exc, (DecisionReplayRefused, DryRunRefused)):
        event, target = PRECHECK_REFUSAL, action.target
    else:
        event, target = PRECHECK_REFUSAL, action.target
    audit(
        request,
        event,
        target,
        _as_blocked(exc),
        operation=operation,
        reason=str(exc),
    )


def _as_blocked(exc: Exception) -> PermissionDecision:
    """Return a stand-in decision carrying a refusal's reason, for the audit record.

    Args:
        exc: The refusal.

    Returns:
        A decision shaped like the one that permitted the call, with the refusal's
        message as its reason — so the audit entry shows *why* the call failed
        rather than the permission reason that let it start.
    """
    from brain.prefrontal_cortex.action import Action as _Action
    from brain.prefrontal_cortex.action import ActionCategory as _Category
    from brain.prefrontal_cortex.permission_decision import PermissionDecision as _PD

    from brain.prefrontal_cortex.trust_matrix import BLOCKED

    return _PD.make(
        _Action(category=_Category.CHAT, target=str(exc)), 0, BLOCKED, str(exc)
    )


def audit_allowlist(request: Request, domain: str, action: str, **fields: Any) -> None:
    """Record an allowlist change.

    Args:
        request: Used to reach the audit log.
        domain: The domain that changed.
        action: ``approve`` or ``forget``.
        **fields: Extra detail.

    Returns:
        Nothing. Recorded because ``url_allowlist.json`` sets
        ``allowlist_changes_logged`` to true, and the list of what ZUZZ will no
        longer ask about *is* the browser's security posture.
    """
    log = getattr(request.app.state, "audit", None)
    if log is None:
        return
    log.record(
        ALLOWLIST_CHANGED,
        f"allowlist {action} for {domain}",
        category="browse_internet",
        target=domain,
        level=action,
        **fields,
    )


def preview(
    operation: str,
    url: str = "",
    session_id: str = "",
    detail: str = "",
    **fields: Any,
) -> Dict[str, Any]:
    """Return what a browser confirmation popup shows.

    Args:
        operation: The operation name.
        url: The URL involved, when there is one.
        session_id: The session it would act on.
        detail: A short description of what is being asked.
        **fields: Extra rows for the popup.

    Returns:
        The operation, the target, the URL, the domain and the extra rows —
        everything the user needs to see what they are approving, and nothing about
        the page itself. A popup that showed page contents would hand over the
        answer before the decision.
    """
    return {
        "operation": operation,
        "url": url,
        "target": url or f"browser:{operation}",
        "domain": _domain_of(url),
        "session": session_id,
        "detail": detail,
        "reads_page": operation in ("extract", "search", "screenshot"),
        "acts_on_page": operation in ("click", "type", "fill", "submit", "key"),
        "writes_disk": operation in ("download", "screenshot"),
        "leaves_machine": False,
        **fields,
    }


async def gated(
    request: Request,
    operation: str,
    body: Any,
    stack: Any,
    method: str,
    *args: Any,
    url: str = "",
    detail: str = "",
    preview_fields: Optional[Dict[str, Any]] = None,
    **call_kwargs: Any,
) -> Dict[str, Any]:
    """Gate, confirm and run one Body method call, in a single expression.

    Most of the browser endpoints are exactly this shape: one method, the request's
    fields as its arguments, and a preview that names the operation. Writing that out
    as a closure and a lambda at every call site is what pushes a route file past the
    point where anyone reads it, so it is built here once.

    The two endpoints that need something more — ``navigate`` and ``search``, whose
    action is built from a URL the Body normalises, and ``download``, which carries a
    second decision — call :func:`run` directly instead.

    Args:
        request: Used to reach the app's collaborators.
        operation: The operation name, for the target string and the log.
        body: The request, including confirmation evidence.
        stack: The :class:`~body.legs.browser_actions.BrowserActions`.
        method: The :class:`~body.legs.browser_actions.BrowserActions` method to call.
        *args: Positional arguments after the decision.
        url: The URL involved, when there is one.
        detail: One line describing what is being asked.
        preview_fields: Extra rows for the confirmation popup, kept apart from the
            method's own keyword arguments so the two cannot collide.
        **call_kwargs: Keyword arguments for the Body method.

    Returns:
        The result, a dry-run plan, or a confirmation prompt.

    Raises:
        HTTPException: Mapped from the Body's refusals.
    """
    return await run(
        request,
        operation,
        body,
        bind(stack, method, *args, **call_kwargs),
        lambda: preview(operation, url, getattr(body, "session_id", ""), detail,
                        **(preview_fields or {})),
        url=url,
        session_id=getattr(body, "session_id", ""),
    )


def bind(stack: Any, method: str, *args: Any, **kwargs: Any) -> Callable:
    """Return an ``execute`` closure that calls one Body method with a decision.

    :func:`run` takes a closure because some operations genuinely need one — a
    download's two decisions, a fill that may or may not submit afterwards. Most do
    not: they are one method call with the request's fields as arguments, and
    writing that call out as a closure three times longer than the call itself is
    how a route file grows past the point where anyone reads it.

    Args:
        stack: The :class:`~body.legs.browser_actions.BrowserActions`.
        method: The method name to call.
        *args: Positional arguments after the decision.
        **kwargs: Keyword arguments after the decision.

    Returns:
        An async closure :func:`run` can call with the allowed decision.
    """
    attribute = getattr(stack, method)

    async def execute(decision: PermissionDecision) -> Any:
        """Call the Body method with the allowed decision.

        Args:
            decision: The allowed decision.

        Returns:
            Whatever the Body method returns.
        """
        return await attribute(decision, *args, **kwargs)

    return execute


def download_actions(stack: Any, url: str, elevated: bool = False) -> tuple:
    """Build both of a download's actions, and its preview.

    A download is two acts — the fetch and the write — so it needs two decisions,
    and one 202 answers both. The write's action is built here rather than in the
    route because it must carry the *quarantine path*, which only the plan knows:
    Fingers' replay check compares the decision's target with the resolved path it
    is about to write, so an action carrying the URL would be refused.

    Args:
        stack: The :class:`~body.legs.browser_actions.BrowserActions`.
        url: What would be downloaded.
        elevated: Whether the user asked for an executable.

    Returns:
        The fetch action, the write action, and the preview dict. The fetch action's
        target is the URL; the write action's target is the absolute quarantine path
        and its category is ``write_files``.

    Raises:
        InvalidUrlRefused: The name the URL implies is not a usable file name.
    """
    plan = stack.plan_download(url, elevated)
    fetch = stack.propose("download", url=plan["url"])
    write = Action(
        category=ActionCategory.WRITE_FILES,
        target=plan["destination"],
        detail={
            "operation": "download-write",
            "from": plan["url"],
            "quarantined": True,
            **({"elevated": True} if elevated else {}),
        },
    )
    return fetch, write, plan


def download_preview(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Return what a download confirmation popup shows.

    Args:
        plan: From :func:`download_actions`.

    Returns:
        The URL, the file name, the quarantine destination, the declared size, the
        executable verdict and the fact that the file will not be executed. Never
        the contents, and never the *values* — this one popup is where a download
        of a credential dump gets its chance to be read before it lands.
    """
    return {
        **preview("download", plan["url"], detail="download into quarantine"),
        "filename": plan["filename"],
        "destination": plan["destination"],
        "quarantine_dir": plan["quarantine_dir"],
        "content_type": plan["content_type"],
        "declared_bytes": plan["declared_bytes"],
        "max_bytes": plan["max_bytes"],
        "executable": plan["executable"],
        "elevated_requested": plan["elevated_requested"],
        "quarantined": True,
        "writes_disk": True,
        "reversible": True,
        "will_execute": False,
        "note": (
            "The file lands in the quarantine directory and is not run, opened or "
            "moved anywhere until you confirm a different path. "
            + (
                "This is an executable: it will still not be run."
                if plan["executable"]
                else ""
            )
        ).strip(),
    }


def allow(
    request: Request,
    stack: Any,
    domain: str,
    category: str = "other",
    remember: bool = True,
    persist: bool = False,
) -> Dict[str, Any]:
    """Record the user's approval of a domain, and audit it.

    Not gated, and the reason is the point: this is the user answering the 202 the
    allowlist produced, not the agent widening its own permissions. A Body method
    that let a caller add to its own allowlist without a decision would be a Body
    method that grants itself permission. So the *audit* record is what stands in for
    the gate here — the change is written down with ``by="user"``, and the UI shows a
    "Forget this domain" button to take it back.

    Args:
        request: Used for the audit record.
        stack: The :class:`~body.legs.browser_actions.BrowserActions`.
        domain: The host.
        category: A label from :data:`body.legs.url_guard.CATEGORIES`.
        remember: False approves it for this session only, which is the honest
            reading of a user who said yes to one page.
        persist: Also keep cookies for this domain between sessions.

    Returns:
        What changed.

    Raises:
        HTTPException: 403 for a domain that is never navigable, so there is nothing
            to approve.
    """
    from body.legs.exceptions import BlockedDomainRefused

    try:
        changed = stack.approve_domain(domain, category, remember)
    except BlockedDomainRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if persist:
        try:
            stack.guard.set_persistence(changed["domain"], True)
            changed["persisted"] = True
        except BlockedDomainRefused as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    audit_allowlist(
        request, changed["domain"], "approve",
        remembered=changed["remembered"], persisted=changed["persisted"], by="user",
    )
    return changed


def forget(request: Request, stack: Any, domain: str) -> Dict[str, Any]:
    """Remove a domain's approval *and* any browser state saved for it.

    The revocation the "Forget this domain" button calls. Both halves matter and
    doing only one is how a "forget" leaves a working cookie jar behind: the
    allowlist entry is removed so the domain asks again, and the encrypted state
    file is deleted so a later approval does not silently restore the login the
    user thought they had discarded.

    Args:
        request: Used for the audit record.
        stack: The :class:`~body.legs.browser_actions.BrowserActions`.
        domain: The host.

    Returns:
        What changed, and the domains still persisted.
    """
    changed = stack.forget_domain(domain)
    from body.legs.session_manager import _clean

    host = _clean(domain)
    deleted = False
    try:
        target = stack.sessions.path_for(host)
        deleted = target.is_file()
        result = stack.sessions.forget(_as_allowed_ask(), host)
        deleted = deleted or bool(result.get("deleted"))
    except Exception:
        # Nothing persisted for this domain, or the store has no key yet. Both are
        # ordinary: a user can forget a domain before ever having used it, and a
        # "forget" that reports "nothing was there" is a success, not a failure.
        pass
    audit_allowlist(
        request,
        changed["domain"],
        "forget",
        changed=",".join(changed["changed"]),
        state_deleted=deleted,
        by="user",
    )
    return {
        "domain": changed["domain"],
        "removed": changed["changed"],
        "state_deleted": deleted,
        "persisted_domains": stack.sessions.persisted_domains(),
    }


def _as_allowed_ask() -> PermissionDecision:
    """Return a decision that permits one already-confirmed user action.

    :meth:`SessionManager.forget` takes a decision because it deletes a credential
    file, and it should. But this path is the *user* revoking consent from the UI,
    not the agent asking for permission — so a forged-allowed decision is the
    correct thing here, and it is confined to this one function so the surface it
    widens is exactly the deletion of a domain the user just revoked.

    Returns:
        An allowed ``browse_internet`` decision at level 5.
    """
    return PermissionDecision.make(
        Action(category=ActionCategory.BROWSE_INTERNET, target="user-revoked"),
        5,
        "auto",
        "the user revoked this domain from the browser page",
    )


def refuse(exc: Exception) -> HTTPException:
    """Map a browser refusal onto an HTTP status.

    The hierarchy does the work: a stop outranks the action (409), the gate's own
    refusals and the guard's hard blocks are 403, and a refusal the Body made on
    its own reading of the URL is a 403 whatever the trust level was. A refused
    *element* is a 404, because the page was read perfectly well and the answer
    genuinely is "there is no such button". A missing browser is a 503 — the system
    is healthy and something it needs is not there.

    Args:
        exc: The refusal.

    Returns:
        The HTTP exception to raise.
    """
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, KillSwitchRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (BlockedDomainRefused, SchemeRefused, PrivateNetworkRefused,
                        DecisionRefused, DryRunRefused, ElementRefused,
                        ExecutableDownloadRefused, QuarantineRefused,
                        SessionPersistenceRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (ElementNotFound, TabNotFound, SessionRefused)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TooManyTabsRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LastTabRefused):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DownloadTooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, BrowserUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, NavigationFailed):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, BAD_INPUT):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="internal error")


def _domain_of(url: str) -> str:
    """Return a URL's host, lower-cased.

    Args:
        url: Any URL, or an empty string.

    Returns:
        The host, or an empty string.
    """
    from urllib.parse import urlsplit

    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


__all__ = [
    "ALLOWLIST_CHANGED",
    "AllowlistRequest",
    "BLOCKED",
    "BrowserActions",
    "BrowserRequest",
    "ClickRequest",
    "CONFIRMATION_REQUESTED",
    "DownloadRequest",
    "ExtractRequest",
    "FormRequest",
    "NavigateRequest",
    "PRECHECK_REFUSAL",
    "SearchRequest",
    "ScreenshotRequest",
    "TAB_LIMIT_REFUSED",
    "TabRequest",
    "URL_BLOCKED",
    "audit",
    "audit_allowlist",
    "confirm",
    "gate",
    "legs",
    "preview",
    "refuse",
    "run",
]