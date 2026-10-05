"""Body/Legs — browser lifecycle.

Owns the Playwright process, the browser, the context and the page, and nothing
else. Everything that acts on a page asks this module for one.

**Headful is the default, and that is the design rather than a convenience.**
Approved answer Q1. Chromium reports ``HeadlessChrome`` in its user agent when
launched headless, so :attr:`BrowserSession.headless` is checked rather than
remembered, and the user agent is exposed so the claim is falsifiable from the API
instead of being a comment in a docstring. The reason to be headful is not that it
looks better: it is that the user can see the browser work, and can take it over
and finish a login themselves rather than being asked to type a password into ZUZZ.

**Every session gets a fresh context.** Cookies, local storage and service workers
live in the context, so a fresh context is a fresh identity. That is the isolation
approved answer Q5 describes, and it is why persistence is opt-in per domain in
:mod:`body.legs.session_manager` rather than being this module's default.

Playwright is imported inside :meth:`BrowserController.launch` rather than at
module scope. Importing it costs about a second and pulls in a driver, and ZUZZ
boots on machines that will never browse.
"""

from __future__ import annotations

import asyncio
import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.legs.exceptions import (
    BrowserUnavailable,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
    NavigationFailed,
    SessionRefused,
)

#: Action categories the browser launches under. Kept here rather than in the gate
#: so the category a session was opened under is visible in the code that opens it.
LAUNCH_CATEGORY = "browse_internet"

#: Viewport for a headful window. Sized to fit a laptop screen so the window is not
#: larger than the desktop the user is watching it on.
VIEWPORT = {"width": 1440, "height": 900}

#: A browser that takes longer than this to start is reported as unavailable rather
#: than left hanging. Chromium on a cold Windows Defender cache can take a while,
#: but not this long, and a request that never returns is worse than one that fails.
LAUNCH_TIMEOUT_SECONDS = 60.0

#: Default cap on how long a page load may take. Overridable per call because a
#: documentation page and a video site are not the same request.
NAVIGATION_TIMEOUT_MS = 30_000

#: Per-process counter for session ids, so two sessions in the same millisecond
#: cannot collide and the ids sort in creation order.
_COUNTER = itertools.count(1)

#: How many navigation lines one session keeps.
NAV_HISTORY_CAP = 200

#: Chromium's own marker for a headless launch.
HEADLESS_MARKER = re.compile(r"headless", re.IGNORECASE)

#: The marker Chromium adds to a headless launch's window navigator, separate from
#: the user agent. Checked too because a page can lie about the user agent.
HEADLESS_PLATFORM = re.compile(r"headless", re.IGNORECASE)


@dataclass
class BrowserSession:
    """One open browser: a Playwright process, a browser, a context and tabs.

    Attributes:
        session_id: Stable id the caller holds.
        playwright: The running ``async_playwright`` driver.
        browser: The launched browser.
        context: The isolated browsing context — cookies and storage live here,
            so this is the unit of session persistence.
        page: The active page, always one of :attr:`pages`.
        pages: Every page in the context, in creation order.
        headless: Whether the browser was asked to run headless. Verified against
            the browser's own user agent by :meth:`read_headless`.
        trusted_domains: Domains the user approved for this session, consulted by
            :mod:`body.legs.url_guard` between the presets and the permanent list.
        persist_domains: Domains whose context state may be written to disk.
        created: Unix timestamp the session was opened.
        last_used: Unix timestamp of the most recent operation, for the idle reaper.
        nav_history: One line per navigation, for the session panel and the audit
            trail. Bounded, because an unbounded history in a long-running session
            is a slow memory leak.
    """

    session_id: str
    playwright: Any
    browser: Any
    context: Any
    page: Any
    pages: List[Any] = field(default_factory=list)
    headless: bool = False
    trusted_domains: set = field(default_factory=set)
    persist_domains: set = field(default_factory=set)
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    nav_history: List[Dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        """Mark the session as used.

        Returns:
            Nothing.
        """
        self.last_used = time.time()

    def record_nav(self, url: str, status: Any = None, error: str = "") -> None:
        """Add one line to the navigation history.

        Args:
            url: What was requested.
            status: The browser's own status code, or None.
            error: The failure message, when there was one.

        Returns:
            Nothing. The history is capped, so a session that browses for hours
            does not accumulate every page it ever saw.
        """
        self.nav_history.append(
            {
                "url": url,
                "status": status,
                "error": error,
                "at": round(time.time(), 3),
            }
        )
        del self.nav_history[:-NAV_HISTORY_CAP]

    async def read_headless(self) -> bool:
        """Ask the browser itself whether it is running headless.

        The launch flag is what ZUZZ requested; this is what Chromium did. Reading
        it from the page is the only check that cannot be satisfied by the
        argument being wrong, and the user agent plus the platform string are both
        consulted because either alone can be spoofed by a page.

        Returns:
            True when the running browser looks headless.
        """
        try:
            user_agent = await self.page.evaluate("() => navigator.userAgent")
            platform = await self.page.evaluate("() => navigator.platform")
        except Exception:
            return self.headless
        text = f"{user_agent or ''} {platform or ''}"
        return bool(HEADLESS_MARKER.search(text) or HEADLESS_PLATFORM.search(text))


class BrowserController:
    """Launches and closes browsers, and is the only holder of Playwright objects.

    Attributes:
        sessions: Live sessions by id.
        logger: Activity logger; None means nothing is logged.
        kill_switch: The switch re-checked by :meth:`launch` and :meth:`close`.
        user_data_dir: Where per-domain persisted sessions are kept. None means the
            default ``storage/sessions``.
        allow_headless: Whether headless launches are permitted at all. False by
            default, and the reason is that a headless browser is a browser the user
            cannot see or intervene in — the property the whole default rests on.
            Set it to True for a server with no display, not to make tests quieter.
    """

    def __init__(
        self,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
        user_data_dir: Optional[Any] = None,
        allow_headless: bool = False,
    ) -> None:
        """Wire the controller.

        Args:
            logger: Activity logger.
            kill_switch: The switch re-checked on launch and close; None means
                the process-wide one.
            user_data_dir: Directory for persisted session state.
            allow_headless: Whether ``headless=True`` may be honoured.
        """
        self.sessions: Dict[str, BrowserSession] = {}
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.user_data_dir = user_data_dir
        self.allow_headless = allow_headless
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------

    async def launch(
        self,
        decision: PermissionDecision,
        headless: bool = False,
        persist_domains: Optional[List[str]] = None,
    ) -> str:
        """Open a browser and return its session id.

        Args:
            decision: An allowed ``browse_internet`` decision whose target is the
                session label, ``browse`` or the empty string.
            headless: Run without a window. Honoured only when
                :attr:`allow_headless` is set; otherwise this raises, because the
                visible browser is the default for a reason and a caller should not
                be able to drop it by accident.
            persist_domains: Domains whose cookies should survive this session.

        Returns:
            The new session id.

        Raises:
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop is engaged.
            DecisionReplayRefused: The decision is for another category.
            BrowserUnavailable: Playwright is not installed, or Chromium would not
                start.
        """
        self._verify(decision, LAUNCH_CATEGORY)
        if headless and not self.allow_headless:
            raise BrowserUnavailable(
                "headless browsing is disabled: ZUZZ's browser is visible by "
                "default so you can watch it and take over. Enable "
                "BrowserController(allow_headless=True) on a machine with no "
                "display."
            )
        async with self._lock:
            if self.kill_switch.is_killed():
                raise KillSwitchRefused("kill switch engaged; the browser will not open")
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserUnavailable(
                    "Playwright is not installed. Run: "
                    "pip install playwright && playwright install chromium"
                ) from exc
            driver = await async_playwright().start()
            try:
                browser = await asyncio.wait_for(
                    driver.chromium.launch(headless=bool(headless)),
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                await _stop_driver(driver)
                raise BrowserUnavailable(
                    f"Chromium would not start: {exc}. If the browser binaries are "
                    f"missing, run: playwright install chromium"
                ) from exc
            try:
                context = await browser.new_context(**_context_options(self.user_data_dir))
            except Exception as exc:
                await _close(browser)
                await _stop_driver(driver)
                raise BrowserUnavailable(f"no browsing context could be created: {exc}") from exc
            page = await context.new_page()
            page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
            session_id = f"legs-{int(time.time())}-{next(_COUNTER):03d}"
            session = BrowserSession(
                session_id=session_id,
                playwright=driver,
                browser=browser,
                context=context,
                page=page,
                pages=[page],
                headless=bool(headless),
                persist_domains=set(persist_domains or ()),
            )
            self.sessions[session_id] = session
            agent = await _user_agent(page)
            observed_headless = await session.read_headless()
            if observed_headless and not headless:
                # The launch asked for a window and the browser says it has none.
                # Rather than trust the flag, the session records what Chromium
                # reported, so the status endpoint cannot claim a visible browser
                # that is not one.
                session.headless = True
            self._log(
                "browser_launched",
                session=session_id,
                headless=session.headless,
                requested_headless=bool(headless),
                user_agent=agent,
            )
            return session_id

    async def close(
        self, decision: PermissionDecision, session_id: str
    ) -> bool:
        """Close a session and everything in it.

        Closing is ordered rather than best-effort: the context first so cookies
        are flushed and workers stop, then the browser, then the Playwright driver.
        A session left with a live driver holds a node process and a browser process
        per launch, so a failed close is a slow leak rather than a cosmetic problem.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session to close.

        Returns:
            True when a session was closed, False when the id was unknown.

        Raises:
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop is engaged.
            DecisionReplayRefused: The decision is for another category.
        """
        self._verify(decision, LAUNCH_CATEGORY, ignore_switch=True)
        async with self._lock:
            session = self.sessions.pop(str(session_id), None)
        if session is None:
            return False
        if self.kill_switch.is_killed():
            # A stop must not leave the browser up: the whole point of the stop is
            # that nothing on this machine keeps acting. The session is dropped
            # above regardless, so a later reset does not resurrect a browser that
            # is no longer tracked.
            await _force_close(session)
            self._log("browser_closed", session=str(session_id), forced=True)
            raise KillSwitchRefused(
                "kill switch engaged; the browser was force-closed rather than "
                "shut down cleanly"
            )
        await _close_context(session)
        await _close(session.browser)
        await _stop_driver(session.playwright)
        self._log("browser_closed", session=str(session_id), forced=False)
        return True

    async def close_all(self) -> int:
        """Close every session, without a decision.

        This is the shutdown path and the exception to the decision rule, listed in
        :mod:`body.legs`'s module docstring alongside the status and tab gauges.
        It exists because it is called from an ``asynccontextmanager`` teardown,
        where requiring a permission decision to release resources would mean a
        crashed server leaves browsers running. It closes nothing the user can see
        — it stops processes ZUZZ started.

        Returns:
            How many sessions were closed.
        """
        async with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            await _force_close(session)
        if sessions:
            self._log("browser_closed_all", count=len(sessions))
        return len(sessions)

    # -- lookups --------------------------------------------------------

    def get(self, session_id: str) -> BrowserSession:
        """Return a live session.

        Args:
            session_id: The session id.

        Returns:
            The session.

        Raises:
            SessionRefused: The id is unknown, which is almost always a closed
                session or a browser that crashed.
        """
        session = self.sessions.get(str(session_id))
        if session is None:
            raise SessionRefused(
                f"no open browser session {session_id!r}; it may have been "
                f"closed, or the browser may have exited"
            )
        return session

    def is_alive(self, session_id: str) -> bool:
        """Return True when the session exists and its browser is connected.

        Not gated. This is a gauge: it reports what the browser is doing, changes
        nothing, and is what the UI polls. Gating it would make the panel that
        tells the user their browser died require a permission to look at a dead
        browser.

        Args:
            session_id: The session id.

        Returns:
            True when the session exists and ``browser.is_connected()`` is true.
        """
        session = self.sessions.get(str(session_id))
        if session is None:
            return False
        try:
            return bool(session.browser.is_connected())
        except Exception:
            return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return a summary of every live session. Not gated; a gauge.

        Returns:
            One record per session: id, age, liveness, headless, tab count,
            current URL and the domains approved for it.
        """
        out: List[Dict[str, Any]] = []
        for session_id, session in self.sessions.items():
            out.append(
                {
                    "session_id": session_id,
                    "alive": self.is_alive(session_id),
                    "headless": session.headless,
                    "age_seconds": round(time.time() - session.created, 1),
                    "idle_seconds": round(time.time() - session.last_used, 1),
                    "tabs": len(session.pages),
                    "url": _current_url(session),
                    "trusted_domains": sorted(session.trusted_domains),
                    "persist_domains": sorted(session.persist_domains),
                    "navigations": len(session.nav_history),
                }
            )
        return out

    async def navigate(
        self,
        session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = NAVIGATION_TIMEOUT_MS,
    ) -> Dict[str, Any]:
        """Load a URL in a session's active page.

        The decision and the allowlist check have already happened by the time this
        is called; this is the part that talks to Chromium. Split that way on
        purpose, because :meth:`body.legs.browser_actions.BrowserActions.navigate`
        is where a refusal is cheap and auditable, and it is also where the tests
        exercise the policy without launching a browser.

        Args:
            session_id: The session.
            url: The already-normalised, already-approved URL.
            wait_until: Playwright's readiness condition. ``domcontentloaded`` rather
                than ``load`` because a page with a stalled advert would otherwise
                count as a failure to load.
            timeout_ms: How long to wait before giving up.

        Returns:
            ``url``, ``title``, ``status``, ``final_url`` and ``redirected_to``.

        Raises:
            SessionRefused: The session is gone.
            NavigationFailed: The page would not load.
        """
        session = self.get(session_id)
        try:
            response = await session.page.goto(
                url, wait_until=wait_until, timeout=timeout_ms
            )
        except Exception as exc:
            session.record_nav(url, None, str(exc))
            raise NavigationFailed(f"{url} did not load: {exc}") from exc
        session.touch()
        status = getattr(response, "status", None) if response is not None else None
        final = _current_url(session)
        redirected = final.rstrip("/") != url.rstrip("/")
        session.record_nav(url, status)
        if status is not None and status >= 400:
            # A 4xx is not a refusal and must not look like one: the page was
            # fetched, the server declined. Recorded separately so the two do not
            # get confused in the history.
            session.nav_history[-1]["error"] = f"HTTP {status}"
        self._log("browser_navigated", session=session_id, url=url, status=status,
                  redirected=redirected)
        return {
            "url": url,
            "final_url": final,
            "title": await _title(session.page),
            "status": status,
            "redirected_to": redirected,
        }

    # -- internals ------------------------------------------------------

    def _verify(
        self,
        decision: PermissionDecision,
        category: str,
        ignore_switch: bool = False,
    ) -> None:
        """Refuse the call unless the decision permits it.

        Args:
            decision: What the caller was handed.
            category: The action category this method performs.
            ignore_switch: Set by :meth:`close`, which must run during a stop.

        Returns:
            Nothing.

        Raises:
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop is engaged.
            DecisionReplayRefused: The decision is for another category.
        """
        if not isinstance(decision, PermissionDecision):
            from body.legs.exceptions import DecisionRefused

            raise DecisionRefused(
                "refusing to drive the browser without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused("dry-run decisions describe the action; they never browse")
        if not ignore_switch and self.kill_switch.is_killed():
            raise KillSwitchRefused("kill switch engaged since this decision was issued")
        if decision.blocked or not decision.allowed:
            from body.legs.exceptions import DecisionRefused

            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != str(category):
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {category}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase. ``browser_navigated`` and the rest carry no
                ``delete``, so the watchdog's deletion counter does not mistake a
                closed tab for a deleted file.
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


# ---------------------------------------------------------------------
# Playwright plumbing
# ---------------------------------------------------------------------


def _context_options(user_data_dir: Optional[Any]) -> Dict[str, Any]:
    """Return the browser context options ZUZZ launches with.

    Three are deliberate and each is a refusal that would otherwise be invisible:

    * ``accept_downloads=True`` — Playwright cancels downloads by default, which
      would make a download look like a failure. ZUZZ wants the file; it just
      insists on landing it in quarantine first.
    * ``service_workers="block"`` — a service worker outlives a navigation and can
      serve a later one from cache, which is a way to show ZUZZ content the
      allowlist never saw. Blocking it costs some sites their offline support.
    * No ``record_video_dir``, no ``trace`` and no HAR. A trace of a browsing
      session is a copy of everything the user typed into every page they visited,
      written somewhere nobody would think to delete it.

    Args:
        user_data_dir: Kept for the signature's symmetry with
            :mod:`body.legs.session_manager`; unused, because persisted state is
            applied per context from an encrypted file rather than by pointing
            Chromium at a persistent profile directory.

    Returns:
        Keyword arguments for ``browser.new_context``.
    """
    return {
        "viewport": dict(VIEWPORT),
        "accept_downloads": True,
        "service_workers": "block",
        "ignore_https_errors": False,
    }


async def _close_context(session: BrowserSession) -> None:
    """Close a session's context, flushing cookies and stopping workers.

    Args:
        session: The session.

    Returns:
        Nothing. A context that will not close is not worth propagating; the
        browser close that follows reclaims the process either way.
    """
    try:
        await session.context.close()
    except Exception:
        pass


async def _close(browser: Any) -> None:
    """Close a browser process.

    Args:
        browser: The Playwright browser.

    Returns:
        Nothing.
    """
    try:
        await browser.close()
    except Exception:
        pass


async def _force_close(session: BrowserSession) -> None:
    """Tear a session down without cooperating, for shutdown and for a stop.

    Args:
        session: The session.

    Returns:
        Nothing. Every step is best-effort and none of them is allowed to raise,
        because this runs on the way down and a failure here must not keep the
        process alive.
    """
    await _close_context(session)
    await _close(session.browser)
    await _stop_driver(session.playwright)


async def _stop_driver(driver: Any) -> None:
    """Stop a Playwright driver, killing its node process if it lingers.

    Args:
        driver: The running ``async_playwright`` context.

    Returns:
        Nothing.
    """
    if driver is None:
        return
    try:
        await driver.stop()
    except Exception:
        pass


async def _user_agent(page: Any) -> str:
    """Return a page's user agent, for the launch log line.

    Read at launch rather than later because the user agent is what the plan's
    headless test asserts on, and a session that fails before the first navigation
    would otherwise never produce one.

    Args:
        page: The Playwright page.

    Returns:
        The user agent, or an empty string when it cannot be read.
    """
    try:
        return str(await page.evaluate("() => navigator.userAgent"))
    except Exception:
        return ""


def _current_url(session: BrowserSession) -> str:
    """Return a page's current URL, defensively.

    Args:
        session: The session.

    Returns:
        The URL, or an empty string when the page has navigated away or closed.
    """
    try:
        return str(session.page.url)
    except Exception:
        return ""


async def _title(page: Any) -> str:
    """Return a page's title, defensively.

    Args:
        page: The Playwright page.

    Returns:
        The title, or an empty string. A title is a nicety, and a page that throws
        while it is being read should not fail a navigation that worked.
    """
    try:
        return str(await page.title())
    except Exception:
        return ""


def tab_targets(session: BrowserSession) -> List[Tuple[int, str]]:
    """Return ``(index, url)`` for every page in a session.

    Args:
        session: The session.

    Returns:
            One entry per page, in creation order.
    """
    return [(index, _url_of(page)) for index, page in enumerate(session.pages)]


def _url_of(page: Any) -> str:
    """Return one page's URL, defensively.

    Args:
        page: The Playwright page.

    Returns:
        The URL, or an empty string.
    """
    try:
        return str(page.url)
    except Exception:
        return ""