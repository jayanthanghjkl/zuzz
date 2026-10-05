"""Body/Legs — the high-level browser API.

:class:`BrowserActions` is what the Face layer calls and what a plugin would call.
It owns the collaborators, holds the order the checks run in, and returns records
rather than Playwright objects. Nothing above this layer knows what a Playwright
page is.

**The order is the point.** Every operation runs:

1. :meth:`BrowserController._verify` — is there a decision, does it allow this,
   was it issued for this category.
2. :meth:`UrlGuard.verify` — is this URL reachable at all, and does the decision
   name *this* URL.
3. The operation.

Gate first, then guard. The other order would be defensible on its own terms but
would make a refusal ambiguous: during a stop, "localhost is never navigable" and
"the kill switch is engaged" are both true and the user needs the second one.
A stop is reported as a stop.

**Propose, then decide, then call.** :meth:`BrowserActions.propose` builds the
:class:`~brain.prefrontal_cortex.action.Action` so the target string the gate
decides about is byte-identical to the one the guard will insist on. This is the
same pattern :meth:`brain.occipital_lobe.occipital_lobe.OccipitalLobe.propose` uses
and it is not ceremony: a decision built from a hand-written target is refused by
the Body's own replay check, which is correct behaviour and a miserable thing to
debug.

Two things this module deliberately does not do:

* **No automatic retry.** A failed navigation is reported. A retry is the caller's
  decision, because "try again" against a hostile site is how a refusal becomes a
  loop.
* **No automatic login.** A page that wants a password gets a browser window with
  a password field in it and a human typing into it. There is no code path where
  ZUZZ types a credential it was given, because a credential ZUZZ was given is a
  credential in its context, the log or both.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.legs.browser_controller import BrowserController, BrowserSession
from body.legs.data_extractor import DataExtractor
from body.legs.download_handler import DownloadHandler
from body.legs.element_interactor import ElementInteractor
from body.legs.exceptions import SessionRefused
from body.legs.form_filler import FormFiller
from body.legs.session_manager import SessionManager
from body.legs.tab_manager import TabManager
from body.legs.url_guard import UrlGuard, Verdict
from body.legs.web_search import DEFAULT_ENGINE, ENGINES, WebSearch

#: The categories this module dispatches on, and which of them guard the URL.
#: ``web_automation`` acts on a page that is already open and never checks a URL,
#: which is why it is not here.
CATEGORIES = {
    "launch": "browse_internet",
    "navigate": "browse_internet",
    "extract": "browse_internet",
    "search": "browse_internet",
    "session": "browse_internet",
    "screenshot": "visual_capture",
    "click": "web_automation",
    "type": "web_automation",
    "select": "web_automation",
    "key": "web_automation",
    "scroll": "web_automation",
    "submit": "web_automation",
    "fill": "web_automation",
    "tabs": "web_automation",
    "download": "download_files",
}

#: Download is two decisions, and this is the second one's category.
WRITE_CATEGORY = "write_files"


class BrowserActions:
    """The browser, as one object, behind the gate.

    Attributes:
        controller: Playwright lifecycle.
        guard: The URL allowlist.
        tabs: The tab manager.
        interactor: Clicking, typing, selecting.
        forms: Form detection and filling.
        extractor: Reading pages.
        sessions: Per-domain persistence.
        downloads: The quarantine.
        search: Web search.
        logger: Activity logger.
        kill_switch: The switch every gated method re-checks.
    """

    def __init__(
        self,
        controller: Optional[BrowserController] = None,
        guard: Optional[UrlGuard] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
        session_root: Optional[Any] = None,
        download_root: Optional[Any] = None,
        allow_headless: bool = False,
        file_manager: Optional[FileManager] = None,
        trust_manager: Optional[Any] = None,
        store: Optional[Any] = None,
    ) -> None:
        """Build a browser stack.

        Args:
            controller: An existing controller; built when None.
            guard: An existing URL guard; built when None.
            logger: Activity logger, shared by every collaborator so one logger
                sees the whole browser.
            kill_switch: The switch every collaborator re-checks; None means the
                process-wide one, the same object the gate uses.
            session_root: Override for the per-domain state directory.
            download_root: Override for the quarantine directory.
            allow_headless: Whether headless launches are permitted. False by
                default, for the reason in :mod:`body.legs.browser_controller`.
            file_manager: The Fingers manager downloads are written through. Handed
                the app's own rather than built here, so a file that arrived in a
                browser is classified by exactly the code that classifies a file the
                agent wrote itself — one classifier, two ways in.
            trust_manager: The app's trust manager, so the session policy the guard
                enforces is the one the user can see in the dashboard rather than a
                private copy that drifts from it.
            store: The Occipital Lobe's screenshot store, for ``screenshot(store=...)``.
                Optional because a capture should work on a server with no vision
                region; the difference is only whether the frame is also filed.
        """
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        # Kept, not just accepted: it is the default for ``screenshot(store=...)``, so
        # a caller that wires the vision region does not have to re-supply it on every
        # call. Read through ``self.store`` at call time rather than closed over, so a
        # region wired after construction is still found.
        self.store = store
        self.guard = guard or UrlGuard(logger=logger, kill_switch=self.kill_switch,
                                       trust_manager=trust_manager)
        self.controller = controller or BrowserController(
            logger=logger,
            kill_switch=self.kill_switch,
            allow_headless=allow_headless,
        )
        self.tabs = TabManager(self.controller, self.guard, logger)
        self.interactor = ElementInteractor(self.controller, self.guard, logger=logger)
        self.forms = FormFiller(self.controller, self.interactor, logger)
        self.extractor = DataExtractor(self.controller, self.guard, logger)
        self.sessions = SessionManager(
            self.controller,
            self.guard,
            root=session_root,
            logger=logger,
            kill_switch=self.kill_switch,
        )
        self.downloads = DownloadHandler(
            self.controller,
            self.guard,
            file_manager=file_manager,
            root=download_root,
            logger=logger,
            kill_switch=self.kill_switch,
        )
        self.search = WebSearch(self.controller, self.guard, self.extractor, logger)

    # -- proposing ------------------------------------------------------

    def propose(
        self,
        operation: str,
        url: str = "",
        detail: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Action:
        """Build the action a caller must get decided before calling this module.

        Args:
            operation: One of :data:`CATEGORIES`.
            url: The URL involved, for a ``browse_internet`` operation.
            detail: Extra context for the Amygdala's scoring.
            dry_run: Mark the action as a description rather than a performance.

        Returns:
            The action, with a target the gate decides about byte-identically to
            the one the guard will check.

        Raises:
            ValueError: For an operation this module does not have.
        """
        if operation not in CATEGORIES:
            raise ValueError(
                f"unknown browser operation {operation!r}; expected one of "
                + ", ".join(sorted(CATEGORIES))
            )
        target = url if url else _target_for(operation)
        return Action(
            category=ActionCategory(CATEGORIES[operation]),
            target=target,
            detail={"operation": operation, **(detail or {})},
            dry_run=dry_run,
        )

    def category_for(self, operation: str) -> str:
        """Return the action category an operation maps onto.

        Args:
            operation: One of :data:`CATEGORIES`.

        Returns:
            The config key.

        Raises:
            ValueError: For an operation this module does not have.
        """
        if operation not in CATEGORIES:
            raise ValueError(f"unknown browser operation {operation!r}")
        return CATEGORIES[operation]

    # -- lifecycle ------------------------------------------------------

    async def open(
        self,
        decision: PermissionDecision,
        headless: bool = False,
        persist_domains: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Open a browser.

        Args:
            decision: An allowed ``browse_internet`` decision.
            headless: Run without a window, only when the controller allows it.
            persist_domains: Domains whose state this session may write.

        Returns:
            ``session_id``, ``headless``, ``headless_verified`` and ``user_agent``.
            ``headless_verified`` is what the browser reported, not what was asked
            for, so the claim is checkable.

        Raises:
            BrowserUnavailable: Playwright or Chromium is not there.
            DecisionRefused: No decision, or one that does not allow this.
        """
        session_id = await self.controller.launch(
            decision, headless, persist_domains
        )
        session = self.controller.get(session_id)
        # Chromium opens a session with one page already there, so the TabManager is
        # told about it here. Without this the browser reports zero tabs for as long
        # as the user does nothing, the cap counts from one too low, and closing the
        # only tab reports "this session has no tabs" instead of refusing properly —
        # which is the refusal that keeps a visible browser visible.
        self.tabs.adopt(session_id, session.page)
        agent = ""
        try:
            agent = str(await session.page.evaluate("() => navigator.userAgent"))
        except Exception:
            agent = ""
        return {
            "session_id": session_id,
            "headless": session.headless,
            "headless_verified": await session.read_headless(),
            "user_agent": agent,
            "tabs": 1,
            "persisted": sorted(session.persist_domains),
        }

    async def close(
        self, decision: PermissionDecision, session_id: str
    ) -> bool:
        """Close a browser session.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.

        Returns:
            True when a session was closed.

        Raises:
            SessionRefused: The session is unknown — though this is returned as
                False rather than raised, because a close of an already-closed
                session is a success from the caller's point of view.
        """
        closed = await self.controller.close(decision, session_id)
        if closed:
            self.tabs.forget_session(session_id)
        return closed

    async def close_all(self) -> int:
        """Close every browser session. Not gated; the shutdown path.

        Documented in :mod:`body.legs`'s module docstring alongside the status and
        tab gauges.

        Returns:
            How many sessions were closed.
        """
        for session_id in list(self.tabs.tabs):
            self.tabs.forget_session(session_id)
        return await self.controller.close_all()

    # -- navigation -----------------------------------------------------

    async def navigate(
        self,
        decision: PermissionDecision,
        session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        explicit_user_command: bool = False,
    ) -> Dict[str, Any]:
        """Go to a URL.

        Args:
            decision: An allowed ``browse_internet`` decision whose target is this
                URL.
            session_id: The session.
            url: Where to go. Normalised and allowlist-checked here.
            wait_until: Playwright's readiness condition.
            explicit_user_command: The user named this URL themselves, so it is
                confirmed even when the domain is listed — approved invariant 2.

        Returns:
            The navigation record, plus ``domain``, ``source``, ``scripting`` and
            ``trusted`` from the guard's verdict.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another URL.
            DomainConfirmationRequired: The domain is unknown and unapproved.
            BlockedDomainRefused: The URL is never navigable.
            NavigationFailed: The page would not load.
            SessionRefused: The session is gone.
        """
        session = self.controller.get(session_id)
        verdict = self.guard.verify(
            decision,
            url,
            str(ActionCategory.BROWSE_INTERNET),
            approved=session.trusted_domains,
            trust_level=decision.trust_level,
            explicit_user_command=explicit_user_command,
        )
        if verdict.allowed and verdict.domain not in session.trusted_domains:
            session.trusted_domains.add(verdict.domain)
        record = await self.controller.navigate(
            session_id, verdict.url, wait_until=wait_until
        )
        return {
            **record,
            "domain": verdict.domain,
            "source": verdict.source,
            "scripting": verdict.scripting,
            "trusted": verdict.allowed,
            "reason": verdict.reason,
        }

    async def scroll(
        self,
        decision: PermissionDecision,
        session_id: str,
        pixels: int = 600,
        to_end: bool = False,
    ) -> Dict[str, Any]:
        """Scroll the page.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            pixels: How far; negative goes back up.
            to_end: Jump to the bottom.

        Returns:
            The scroll record.
        """
        return await self.interactor.scroll(decision, session_id, pixels, to_end)

    # -- interaction ----------------------------------------------------

    async def click_element(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str = "",
        coordinates: Optional[Tuple[int, int]] = None,
        confidence: Optional[float] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """Click an element, by selector, by text or by a vision coordinate.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            selector_or_text: A CSS selector or visible text.
            coordinates: ``(x, y)`` from the Occipital Lobe.
            confidence: The Occipital Lobe's confidence for those coordinates.
            description: What the caller was trying to click, for the log.

        Returns:
            The click record.
        """
        return await self.interactor.click(
            decision,
            session_id,
            selector_or_text,
            coordinates=coordinates,
            confidence=confidence,
            description=description,
        )

    async def type_text(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str,
        text: str,
        submit: bool = False,
    ) -> Dict[str, Any]:
        """Type into a field.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            selector_or_text: The field.
            text: What to type. Never logged.
            submit: Press Enter afterwards.

        Returns:
            The typing record.
        """
        return await self.interactor.type_text(
            decision, session_id, selector_or_text, text, submit
        )

    async def fill_form(
        self,
        decision: PermissionDecision,
        session_id: str,
        form_data: Dict[str, str],
        confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fill form fields and stop.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            form_data: ``{field handle: value}``.
            confidence: The Occipital Lobe's confidence, when the field was located
                by vision.

        Returns:
            The fill record, with ``filled``, ``missing`` and ``submitted: False``.
        """
        return await self.forms.fill(
            decision, session_id, form_data, confidence=confidence
        )

    # -- reading --------------------------------------------------------

    async def extract(
        self,
        decision: PermissionDecision,
        session_id: str,
        mode: str = "text",
        selector: str = "",
        limit: int = 0,
    ) -> Dict[str, Any]:
        """Read something out of the page.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            mode: ``text``, ``links``, ``html``, ``table``, ``metadata``,
                ``fields`` or ``images``.
            selector: A CSS selector, where the mode takes one.
            limit: Override the cap for this call.

        Returns:
            The extraction record.

        Raises:
            ValueError: For a mode this module does not have.
        """
        if mode == "text":
            return await self.extractor.text(decision, session_id, selector or "body", limit)
        if mode == "links":
            return await self.extractor.links(decision, session_id, limit=limit)
        if mode == "html":
            return await self.extractor.html(decision, session_id, selector, limit)
        if mode == "table":
            return await self.extractor.table(decision, session_id, selector or "table", limit)
        if mode == "metadata":
            return await self.extractor.metadata(decision, session_id)
        if mode == "fields":
            return await self.extractor.fields(decision, session_id)
        if mode == "images":
            return await self.extractor.images(decision, session_id)
        raise ValueError(
            f"unknown extraction mode {mode!r}; expected text, links, html, table, "
            f"metadata, fields or images"
        )

    async def extract_text(
        self, decision: PermissionDecision, session_id: str, selector: str = "body"
    ) -> str:
        """Return the text of an element, as a bare string.

        The plan's interface returns ``str`` here rather than the record, so this is
        the narrow wrapper and :meth:`extract` is what the API calls.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            selector: A CSS selector.

        Returns:
            The text, truncated at the configured cap.
        """
        record = await self.extractor.text(decision, session_id, selector)
        return str(record["text"])

    async def extract_links(
        self, decision: PermissionDecision, session_id: str
    ) -> List[Dict[str, str]]:
        """Return the page's links.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.

        Returns:
            The link records.
        """
        record = await self.extractor.links(decision, session_id)
        return list(record["links"])

    async def search_web(
        self,
        decision: PermissionDecision,
        session_id: str,
        query: str,
        engine: str = DEFAULT_ENGINE,
        count: int = 10,
    ) -> Dict[str, Any]:
        """Search the web.

        The engine's own domain is checked here, inside the call, so an unapproved
        engine produces the same
        :class:`~body.legs.exceptions.DomainConfirmationRequired` a navigation to
        an unapproved site does — and therefore the same 202, from the same place in
        the flow. Checking it in the route instead would mean one code path had a
        question and the other did not.

        Args:
            decision: An allowed ``browse_internet`` decision whose target is the
                search URL.
            session_id: The session.
            query: What to search for. Never logged.
            engine: ``duckduckgo``, ``google`` or ``bing``.
            count: How many results.

        Returns:
            The search record.

        Raises:
            DomainConfirmationRequired: The engine's domain is unapproved.
            InvalidUrlRefused: The engine name is not one this module knows.
        """
        session = self.controller.get(session_id)
        url = self.search_url(query, engine, count)
        verdict = self.guard.verify(
            decision,
            url,
            str(ActionCategory.BROWSE_INTERNET),
            approved=session.trusted_domains,
            trust_level=decision.trust_level,
        )
        if verdict.allowed and verdict.domain not in session.trusted_domains:
            session.trusted_domains.add(verdict.domain)
        return await self.search.search(decision, session_id, query, engine, count)

    def search_url(self, query: str, engine: str = DEFAULT_ENGINE, count: int = 10) -> str:
        """Return the URL a search would load.

        Args:
            query: What to search for.
            engine: The engine name.
            count: How many results, which is part of the URL.

        Returns:
            The search URL, normalised so the target the gate decides about is the
            URL the guard will check.

        Raises:
            InvalidUrlRefused: The engine name is not one this module knows.
        """
        from body.legs.exceptions import InvalidUrlRefused

        name = str(engine or DEFAULT_ENGINE).strip().lower()
        if name not in ENGINES:
            raise InvalidUrlRefused(
                f"{engine!r} is not a search engine ZUZZ knows; use one of "
                + ", ".join(sorted(ENGINES))
            )
        wanted = max(1, min(int(count or 10), 50))
        return ENGINES[name]["url"].format(query=quote_plus(str(query).strip()),
                                           count=wanted)

    def propose_search(
        self, query: str, engine: str = DEFAULT_ENGINE, count: int = 10
    ) -> Action:
        """Build the action for a search.

        The engine's domain is **not** checked here. :meth:`search_web` checks it
        inside the call, where a refusal becomes the standard 202 for an unapproved
        domain rather than a second, differently-shaped question.

        Args:
            query: What to search for.
            engine: The engine name.
            count: How many results, which is part of the URL and so part of the
                target.

        Returns:
            The action, with the search URL as its target.

        Raises:
            InvalidUrlRefused: The engine name is not one this module knows.
        """
        url = self.search_url(query, engine, count)
        return self.propose("search", url=url, detail={"engine": engine})

    # -- downloads ------------------------------------------------------

    async def download_file(
        self,
        decision: PermissionDecision,
        write_decision: PermissionDecision,
        session_id: str,
        url: str,
        elevated: bool = False,
    ) -> Dict[str, Any]:
        """Download a file into quarantine.

        Args:
            decision: An allowed ``download_files`` decision for this URL.
            write_decision: An allowed ``write_files`` decision for the path the
                plan will write to.
            session_id: The session whose cookies the request carries.
            url: What to download.
            elevated: The user specifically asked for an executable.

        Returns:
            The download record, including ``classification`` and the
            ``sha256`` of what was written.

        Raises:
            ExecutableDownloadRefused: An executable with no explicit request.
            DownloadTooLarge: Over the cap.
            QuarantineRefused: The bytes could not be placed in the holding pen.
        """
        plan = self.downloads.plan(url, elevated=elevated)
        return await self.downloads.fetch(
            decision, write_decision, session_id, url, plan, elevated
        )

    def plan_download(self, url: str, elevated: bool = False) -> Dict[str, Any]:
        """Return what a download would do. Not gated; a preview.

        Args:
            url: What would be downloaded.
            elevated: Whether an executable was asked for.

        Returns:
            The plan's preview, for building the confirmation payload and the
            ``write_files`` decision's target.
        """
        return self.downloads.plan(url, elevated=elevated).preview()

    def list_downloads(self) -> List[Dict[str, Any]]:
        """List what is in quarantine. Not gated; a gauge.

        Returns:
            One record per held file.
        """
        return self.downloads.list_quarantine()

    # -- screenshots ----------------------------------------------------

    async def screenshot(
        self,
        decision: PermissionDecision,
        session_id: str,
        full_page: bool = False,
        label: str = "",
        store: Optional[Any] = None,
    ) -> bytes:
        """Return a PNG of the page.

        Categorised ``visual_capture``, not ``browse_internet``, because a browser
        window routinely holds a logged-in banking session or a password manager
        and the same argument applies that made the Occipital Lobe's category
        separate from ``read_files``.

        Args:
            decision: An allowed ``visual_capture`` decision.
            session_id: The session.
            full_page: Capture the whole scrollable page rather than the viewport.
            label: A short name, used only when ``store`` is given.
            store: An Occipital ``Screenshots`` store to file the frame in. None falls
                back to the store this stack was built with, and None in its own right
                keeps the bytes in memory: writing a browser frame to disk is a write
                the caller did not ask for, and the route serves it from memory
                instead. Pass ``store=False`` to ask for memory-only on a stack that
                has a store wired.

        Returns:
            PNG bytes.
        """
        self.controller._verify(decision, "visual_capture")
        if store is None:
            store = self.store
        session = self.controller.get(session_id)
        data = await session.page.screenshot(full_page=bool(full_page), type="png")
        image = bytes(data)
        session.touch()
        if store is not None:
            from brain.occipital_lobe.capture import Capture

            store.save(
                Capture(
                    image=image,
                    width=session.page.viewport_size["width"] if
                    getattr(session.page, "viewport_size", None) else 0,
                    height=session.page.viewport_size["height"] if
                    getattr(session.page, "viewport_size", None) else 0,
                    label=label or "browser",
                    extra={"session": session_id, "url": _url_of(session.page)},
                )
            )
        self._log("browser_screenshot", session=session_id, bytes=len(image),
                  stored=store is not None, label=label)
        return image

    # -- status ---------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return everything the UI needs about the browser. Not gated; a gauge.

        Returns:
            Live sessions, tab limits, download policy, session persistence,
            supported search engines and the allowlist summary. Reads only ZUZZ's
            own state — no page contents, no cookies, nothing from inside a page.
        """
        return {
            "sessions": self.controller.list_sessions(),
            "tabs": self.tabs.limits(),
            "open_tabs": self.tabs.open_count(),
            "downloads": self.downloads.limits(),
            "quarantine": len(self.downloads.list_quarantine()),
            "sessions_persisted": self.sessions.describe(),
            "engines": self.search.engines(),
            "allowlist": self.guard.describe(),
            "extraction": self.extractor.caps(),
            "stopped": self.kill_switch.is_killed(),
            "headless_allowed": self.controller.allow_headless,
        }

    def list_tabs(self, session_id: str = "") -> Dict[str, Any]:
        """Return the tabs. Not gated; a gauge.

        Args:
            session_id: One session, or ``""`` for all.

        Returns:
            The tab records and the cap fields.
        """
        return self.tabs.list_tabs(session_id)

    async def switch_tab(
        self, decision: PermissionDecision, session_id: str, tab_id: str = ""
    ) -> Dict[str, Any]:
        """Switch the active tab.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            tab_id: The tab, or ``""`` for the first.

        Returns:
            The tab record.
        """
        return await self.tabs.switch(decision, session_id, tab_id)

    async def open_tab(
        self,
        decision: PermissionDecision,
        session_id: str,
        url: str = "about:blank",
        override: bool = False,
    ) -> Dict[str, Any]:
        """Open a tab, refusing past the cap.

        Args:
            decision: An allowed ``web_automation`` decision. With ``override`` set,
                this must carry ``detail["tab_override"]``, which the Face layer
                only sets after the 202 for the override comes back answered.
            session_id: The session.
            url: The page to show.
            override: Raise the cap for this call.

        Returns:
            The tab record, plus the cap fields.

        Raises:
            TooManyTabsRefused: The cap is reached.
        """
        return await self.tabs.open(decision, session_id, url, override=override)

    async def close_tab(
        self, decision: PermissionDecision, session_id: str, tab_id: str = ""
    ) -> Dict[str, Any]:
        """Close one tab.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            tab_id: The tab.

        Returns:
            What was closed and what remains.
        """
        return await self.tabs.close_tab(decision, session_id, tab_id)

    # -- allowlist ------------------------------------------------------

    def approve_domain(
        self, domain: str, category: str = "other", remember: bool = True
    ) -> Dict[str, Any]:
        """Add a domain the user just approved.

        Not gated. This is the user answering a question ZUZZ asked, not the agent
        widening its own permissions — which is also why the answer is recorded in
        the audit log by the Face layer rather than here. A Body method that let a
        caller add to the allowlist without a decision would be a Body method that
        grants itself permission.

        Args:
            domain: The host.
            category: A label for the UI.
            remember: False records the approval for this session only.

        Returns:
            What changed.
        """
        return self.guard.approve(domain, category, remember=remember)

    def forget_domain(self, domain: str) -> Dict[str, Any]:
        """Undo an approval and delete any state saved for it.

        The revocation the UI's "Forget this domain" button calls.

        Args:
            domain: The host.

        Returns:
            What changed.
        """
        return self.guard.forget(domain)

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail.

        Returns:
            Nothing.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("legs", action, **fields)
        except Exception:
            pass


def _target_for(operation: str) -> str:
    """Return the target string for an operation that has no URL.

    Args:
        operation: The operation name.

    Returns:
        A short target such as ``browser:open``, which the guard does not need to
        check and the gate can still show in a confirmation.
    """
    return f"browser:{operation}"


def _url_of(page: Any) -> str:
    """Return a page's URL, defensively.

    Args:
        page: The Playwright page.

    Returns:
        The URL, or an empty string.
    """
    try:
        return str(page.url)
    except Exception:
        return ""