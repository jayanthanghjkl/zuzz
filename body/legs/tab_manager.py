"""Body/Legs — tabs.

Five tabs, because five is the number at which a browser session stops being a
browsing session and becomes the reason the machine is slow. Approved answer Q6,
and the cap is enforced here rather than in the API so a caller that reaches the
browser by another route gets the same refusal.

Three things about the cap that are easy to get wrong and are therefore deliberate:

* **The soft warning never blocks.** Three tabs is where performance starts to
  suffer, so three tabs is where the UI says so. A warning that refused would just
  be a cap of three with a worse name.
* **The override is a real override, and it is written down.** "The user can
  override" would be meaningless if it meant "the cap is advisory", so an override
  requires its own confirmed ``web_automation`` decision and lands in the audit log
  with the domain and the tab count. The cap is not enforced in proportion to how
  willing anyone feels.
* **A refused tab is a refusal, not a silent truncation.** Returning the wrong page
  because the sixth tab could not open would leave ZUZZ reading a page it does not
  think it is on, which is worse than not opening the tab.

Tabs are tracked per :class:`~body.legs.browser_controller.BrowserSession` rather
than in a module-level dict, so two browser sessions cannot see each other's tabs
and closing one cannot invalidate the other's handles.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_controller import BrowserController, BrowserSession
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    LastTabRefused,
    SessionRefused,
    TabNotFound,
    TooManyTabsRefused,
)

#: Action category for everything that opens, focuses or closes a tab.
TAB_CATEGORY = "web_automation"

#: Fallbacks used when the config has no answer, so a truncated or missing
#: ``tabs`` block cannot silently widen the cap. Five is the approved value and
#: three is the approved warning point; both are repeated here so that deleting
#: them from the config makes the browser *more* restrictive, not unlimited.
DEFAULT_HARD_CAP = 5
DEFAULT_SOFT_WARNING = 3

#: Per-session counter for tab ids, so a tab id is short and still sorts.
_TAB_COUNTER = itertools.count(1)


@dataclass
class Tab:
    """One page in a session.

    Attributes:
        tab_id: Stable id for the session's lifetime, e.g. ``tab-2``.
        page: The Playwright page object.
        index: Position in the session's tab list, which is what the UI shows.
        opened: Unix timestamp.
        url: The URL as last known, refreshed on every :meth:`describe`.
    """

    tab_id: str
    page: Any
    index: int
    opened: float = field(default_factory=time.time)
    url: str = ""

    def describe(self, active: bool = False) -> Dict[str, Any]:
        """Return what the UI shows for this tab.

        Args:
            active: Whether this is the session's active page.

        Returns:
            ``tab_id``, ``index``, ``url``, ``opened`` and ``active``. No title:
            tabs are listed from a synchronous gauge and Playwright's title is a
            coroutine, so a title here would always be empty. The URL is the
            identifier anyway.
        """
        url = ""
        try:
            url = str(self.page.url)
        except Exception:
            url = self.url
        return {
            "tab_id": self.tab_id,
            "index": self.index,
            "url": url,
            "opened": round(self.opened, 3),
            "active": bool(active),
        }


class TabManager:
    """Opens, switches and closes tabs, and enforces the cap.

    Attributes:
        controller: The controller that owns the sessions.
        logger: Activity logger; None means nothing is logged.
        config: The allowlist mapping, read fresh for the cap.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        logger: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Wire the tab manager.

        Args:
            controller: The controller holding the sessions.
            guard: The :class:`~body.legs.url_guard.UrlGuard`, used to read the
                ``tabs`` block. None means read ``url_allowlist.json`` directly.
            logger: Activity logger.
            config: The allowlist mapping, for tests; overrides ``guard``.
        """
        self.controller = controller
        self.guard = guard
        self.logger = logger
        self._config = config
        self.tabs: Dict[str, List[Tab]] = {}

    # -- adopting --------------------------------------------------------

    def adopt(self, session_id: str, page: Any, url: str = "") -> Tab:
        """Record a page the browser opened by itself as the session's first tab.

        Chromium creates one page when a context is opened, and the manager has to
        know about it or every count is a tab short: the gauge would say a fresh
        browser has none, the cap would admit a sixth page to a five-tab session, and
        closing the only tab would report "no tabs" instead of the refusal that keeps
        a visible browser visible.

        Called from :meth:`~body.legs.browser_actions.BrowserActions.open` rather than
        from the controller, because the controller knows about Playwright objects
        and this manager knows about caps — and only the second one cares.

        Args:
            session_id: The session the page belongs to.
            page: The page Chromium opened.
            url: Its URL, when known.

        Returns:
            The recorded tab. An existing entry for the same page is returned rather
            than duplicated, so a double call cannot inflate the count.
        """
        existing = self.tabs.get(session_id, [])
        for tab in existing:
            if tab.page is page:
                return tab
        tab = Tab(
            tab_id=f"tab-{next(_TAB_COUNTER)}",
            page=page,
            index=len(existing) + 1,
            url=url,
        )
        existing.append(tab)
        self.tabs[session_id] = existing
        self._log("browser_tab_adopted", session=session_id, tab=tab.tab_id,
                  open=len(existing))
        return tab

    # -- configuration --------------------------------------------------

    def limits(self) -> Dict[str, Any]:
        """Return the tab limits as configured.

        Returns:
            ``hard_cap``, ``soft_warning_at``, ``user_override_allowed`` and
            ``override_requires_separate_confirmation``. Missing values fall back
            to the approved numbers rather than to "no limit".
        """
        settings = self._tabs_config()
        return {
            "hard_cap": _as_int(settings.get("hard_cap"), DEFAULT_HARD_CAP),
            "soft_warning_at": _as_int(
                settings.get("soft_warning_at"), DEFAULT_SOFT_WARNING
            ),
            "user_override_allowed": bool(settings.get("user_override_allowed", True)),
            "override_requires_separate_confirmation": bool(
                settings.get("override_requires_separate_confirmation", True)
            ),
            "open": self.open_count(),
        }

    def open_count(self) -> int:
        """Return how many tabs are open across every session. Not gated; a gauge.

        Returns:
            The total tab count. Cheaper than walking the sessions, and it is what
            the cap is compared against in aggregate.
        """
        return sum(len(tabs) for tabs in self.tabs.values())

    # -- operations -----------------------------------------------------

    async def open(
        self,
        decision: PermissionDecision,
        session_id: str,
        url: str = "about:blank",
        activate: bool = True,
        override: bool = False,
    ) -> Dict[str, Any]:
        """Open a tab.

        Args:
            decision: An allowed ``web_automation`` decision. When ``override`` is
                set this must be the *separate* confirmed decision the config asks
                for, not the one that would have opened the tab.
            session_id: The session to open it in.
            url: The page to show. ``about:blank`` when not navigating yet — the
                empty page is created by Chromium and never passes the allowlist,
                because there is nothing to fetch.
            activate: Make the new tab the active one.
            override: Raise the cap for this call. Only honoured when the config
                allows it and the decision carries the user's confirmation.

        Returns:
            The new tab's record, plus ``cap``, ``open`` and ``at_soft_limit``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone.
            TooManyTabsRefused: The hard cap is reached and no override was given.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        limits = self.limits()
        cap = limits["hard_cap"]
        open_tabs = self.tabs.setdefault(session_id, [])
        if len(open_tabs) >= cap:
            if not override or not limits["user_override_allowed"]:
                raise TooManyTabsRefused(
                    cap, len(open_tabs), override_possible=limits["user_override_allowed"]
                )
            if not decision.action.detail.get("tab_override"):
                # An override is not permission to open one more tab; it is
                # permission to raise a limit the user set. Reusing the decision
                # that asked for the tab would make the cap advisory without anyone
                # being asked, so the caller must arrive holding a separately
                # confirmed decision that says so, and the Face layer only builds
                # one after the 202 has come back answered.
                raise TooManyTabsRefused(cap, len(open_tabs), override_possible=True)
        page = await session.context.new_page()
        tab = Tab(
            tab_id=f"tab-{next(_TAB_COUNTER)}",
            page=page,
            index=len(open_tabs) + 1,
            url=url,
        )
        open_tabs.append(tab)
        _renumber(open_tabs)
        session.pages = [item.page for item in open_tabs]
        if activate:
            session.page = page
        if url and url != "about:blank":
            await page.goto(url, wait_until="domcontentloaded")
        session.touch()
        self._log(
            "browser_tab_opened",
            session=session_id,
            tab=tab.tab_id,
            url=url,
            open=len(open_tabs),
            cap=cap,
            override=bool(override),
        )
        return {
            **tab.describe(active=activate),
            "cap": cap,
            "open": len(open_tabs),
            "at_soft_limit": len(open_tabs) >= limits["soft_warning_at"],
            "overridden": bool(override),
        }

    async def switch(
        self, decision: PermissionDecision, session_id: str, tab_id: str = ""
    ) -> Dict[str, Any]:
        """Make a tab the session's active page.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            tab_id: The tab, or ``""`` for the first one.

        Returns:
            The tab's record.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone.
            TabNotFound: No tab by that id. Surfaced as ``SessionRefused``'s sibling
                with its own message so the UI can tell "no browser" from "no such
                tab", which are different user problems.
        """
        self._verify(decision)
        tabs = self.tabs.get(session_id)
        if not tabs:
            raise SessionRefused(f"session {session_id} has no tabs")
        target = _find(tabs, tab_id)
        session = self.controller.get(session_id)
        try:
            await target.page.bring_to_front()
        except Exception:
            # A headless context has no window to raise; the active page pointer is
            # still the thing the rest of the Body acts through, so this is not a
            # failure — the click will land in the right page regardless.
            pass
        session.page = target.page
        session.touch()
        self._log("browser_tab_switched", session=session_id, tab=target.tab_id)
        return target.describe(active=True)

    async def close_tab(
        self, decision: PermissionDecision, session_id: str, tab_id: str = ""
    ) -> Dict[str, Any]:
        """Close one tab.

        The last tab is not closed. A browser session with no tabs is a browser
        session that cannot show the user what it is doing, and the user being able
        to intervene is the reason the browser is visible in the first place — so
        closing the browser is :meth:`BrowserController.close`, a separate,
        separately-confirmed operation, not a side effect of tidying up.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            tab_id: The tab, or ``""`` for the first one.

        Returns:
            What was closed, how many remain, and which is now active.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone or has no tabs.
            TabNotFound: The id names no tab, or it is the last one.
        """
        self._verify(decision)
        tabs = self.tabs.get(session_id)
        if not tabs:
            raise SessionRefused(f"session {session_id} has no tabs")
        if len(tabs) == 1:
            raise LastTabRefused(
                f"{tab_id or tabs[0].tab_id} is the only tab open; close the "
                f"browser itself to shut the session down"
            )
        target = _find(tabs, tab_id)
        tabs.remove(target)
        try:
            await target.page.close()
        except Exception:
            # The page may already be gone if it crashed. It still leaves the list,
            # which is the thing that has to be true.
            pass
        session = self.controller.get(session_id)
        _renumber(tabs)
        session.pages = [item.page for item in tabs]
        if session.page is target.page and tabs:
            session.page = tabs[-1].page
        session.touch()
        self._log("browser_tab_closed", session=session_id, tab=target.tab_id,
                  remaining=len(tabs))
        return {
            "closed": target.tab_id,
            "open": len(tabs),
            "active": tabs[-1].tab_id if tabs else "",
        }

    def list_tabs(self, session_id: str = "") -> Dict[str, Any]:
        """Return the tabs, for the UI. Not gated; a gauge.

        Names pages and shows where they are pointed. It reads nothing out of them,
        which is the distinction that keeps this ungated: reading a page's *contents*
        is a ``browse_internet`` decision, and asking where a tab is pointed is the
        panel a user consults to decide what to do next.

        Args:
            session_id: One session, or ``""`` for all of them.

        Returns:
            ``sessions``, each with its tab list and the cap fields.
        """
        limits = self.limits()
        wanted = [session_id] if session_id else list(self.tabs)
        sessions = []
        for sid in wanted:
            tabs = self.tabs.get(sid, [])
            session = self.controller.sessions.get(sid)
            active_id = ""
            if session is not None:
                for item in tabs:
                    if item.page is session.page:
                        active_id = item.tab_id
                        break
            sessions.append(
                {
                    "session_id": sid,
                    "alive": self.controller.is_alive(sid),
                    "active_tab": active_id,
                    "tabs": [item.describe(active=item.tab_id == active_id) for item in tabs],
                }
            )
        return {**limits, "sessions": sessions}

    def forget_session(self, session_id: str) -> None:
        """Drop a session's tab bookkeeping without closing anything.

        Args:
            session_id: The session that was closed elsewhere.

        Returns:
            Nothing.
        """
        self.tabs.pop(session_id, None)

    # -- internals ------------------------------------------------------

    def _tabs_config(self) -> Dict[str, Any]:
        """Return the ``tabs`` block of the allowlist.

        Returns:
            The configured limits, or an empty mapping when the file has no
            ``tabs`` block. Callers fall back to the approved numbers, so a missing
            block is restrictive rather than unlimited.
        """
        config = self._config
        if config is None and self.guard is not None:
            config = self.guard.config()
        if config is None:
            from core.config import CONFIG_DIR
            import json

            try:
                config = json.loads(
                    (CONFIG_DIR / "url_allowlist.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                return {}
        tabs = config.get("tabs", {}) if isinstance(config, dict) else {}
        return tabs if isinstance(tabs, dict) else {}

    def _verify(self, decision: PermissionDecision) -> None:
        """Refuse the call unless the decision permits it.

        Args:
            decision: What the caller was handed.

        Returns:
            Nothing.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
        """
        if not isinstance(decision, PermissionDecision):
            raise DecisionRefused(
                "refusing to manage tabs without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused("dry-run decisions describe the action; they never browse")
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != TAB_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {TAB_CATEGORY}"
            )

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


def _find(tabs: List[Tab], tab_id: str) -> Tab:
    """Return the tab with this id.

    Args:
        tabs: The session's tabs.
        tab_id: The id, or ``""`` for the first tab.

    Returns:
        The tab.

    Raises:
        TabNotFound: No tab has that id. The message names the tabs that do exist,
            because "no such tab" with an empty list is the least useful possible
            answer to a UI that sent a stale id.
    """
    if not str(tab_id).strip():
        return tabs[0]
    for item in tabs:
        if item.tab_id == str(tab_id):
            return item
    raise TabNotFound(
        f"no tab {tab_id!r} in this session; it has "
        + ", ".join(item.tab_id for item in tabs)
    )


def _renumber(tabs: List[Tab]) -> None:
    """Renumber a session's tabs into display order.

    Args:
        tabs: The session's tabs.

    Returns:
        Nothing. The id is stable for the session's life; the index is a position,
        and a position that says 3 for a tab the user sees second is a bug in the UI
        rather than something to debug there.
    """
    for index, item in enumerate(tabs):
        item.index = index + 1


def _as_int(value: Any, fallback: int) -> int:
    """Return an integer setting, falling back when it is absent or nonsense.

    Args:
        value: The configured value.
        fallback: What to use when the value is not a positive integer.

    Returns:
        A sane positive integer. A cap of zero or a cap of -1 would refuse every tab,
    so a bad value is treated as missing rather than obeyed.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback