"""Body/Legs — the browser package.

Three things are worth testing here, and they are not the same three things most
suites would pick.

**The refusals.** A browser package is mostly a pile of things it will not do, and a
refusal that quietly stops being one is the failure mode that matters. Every block
rule, the private-network refusal, the tab cap, the executable refusal and the
off-page coordinate each get a test, and the recurring assertion is that the refused
thing did not happen.

**The flow through the API.** A request must be scored, decided, and either executed
or turned into a 202 before anything reaches a page. The three states every endpoint
can be in — ``ok``, ``confirmation_required`` and refused — are checked on endpoints
that stand for whole families of operations.

**The odd invariants.** That ``fill`` does not submit. That a confirmation is
single-use. That an approval for one URL cannot be spent on another. Those are
properties of the design rather than of any one function, and they are the ones that
would be easy to break in a later refactor without any test going red.

Nothing in this file launches a browser. A :class:`StubController` implements the
same surface as :class:`~body.legs.browser_controller.BrowserController` over
in-memory pages, so Playwright need not be installed and no test can reach the
network, the filesystem outside ``tmp_path``, or the developer's own browser
profile. Tests that genuinely need a browser are integration tests and are marked as
such; these are not.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.trust_manager import TrustManager

from body.legs import BrowserActions
from body.legs.exceptions import (
    BlockedDomainRefused,
    DecisionRefused,
    DomainConfirmationRequired,
    DryRunRefused,
    ElementNotFound,
    ElementRefused,
    ExecutableDownloadRefused,
    FormFillRefused,
    InvalidUrlRefused,
    KillSwitchRefused,
    LastTabRefused,
    PrivateNetworkRefused,
    SchemeRefused,
    TabNotFound,
    TooManyTabsRefused,
    UrlRefused,
)
from body.legs.tab_manager import TabManager
from face.api.routes_browser import router
from face.api.server import app

CONFIG = Path(__file__).resolve().parent.parent / "config"
FRONTEND = Path(__file__).resolve().parent.parent / "face" / "frontend"


def registered_paths(router: Any) -> set:
    """Return every path a router serves, however the routes are nested.

    ``routes_browser`` is assembled from three sub-routers with
    ``include_router``, and as of FastAPI 0.142 an included router is appended to
    ``.routes`` as an ``_IncludedRouter`` wrapper rather than flattened into its
    children — so ``{route.path for route in router.routes}`` raises
    ``AttributeError`` before it can check anything. Walking ``original_router``
    keeps the check working whatever the nesting depth, which is the point: this
    test exists to notice a control pointing at a 404, and it must not be the
    thing that breaks on a dependency bump.
    """
    paths: set = set()
    stack: List[Any] = list(getattr(router, "routes", []))
    while stack:
        entry = stack.pop()
        path = getattr(entry, "path", None)
        if path is not None:
            paths.add(str(path))
            continue
        nested = getattr(getattr(entry, "original_router", None), "routes", None)
        if nested:
            stack.extend(nested)
    return paths


# -- allowlist fixtures ------------------------------------------------


@pytest.fixture
def allowlist() -> Dict[str, Any]:
    """A small allowlist in the same shape as ``config/url_allowlist.json``.

    Built to the shipped config's structure rather than a flat one, so the fixture
    cannot drift into a shape the guard does not read — a test allowlist that is
    quietly ignored because it is keyed wrongly passes every test and proves nothing.
    Two presets are the minimum that lets the interesting cases exist: one the user
    approved in advance (``example.com``) and one they have not (``unknown.test``).
    """
    return {
        "policy": {
            "unknown_domain": "ask",
            "allowlist_changes_logged": True,
            "match_subdomains": True,
            "match_is_case_insensitive": True,
            "allowed_schemes": ["https", "http"],
        },
        "presets": {
            "example.com": {"category": "reference", "added": "preset"},
            "duckduckgo.com": {"category": "search", "added": "preset"},
        },
        "user_added": {},
        "user_removed_presets": [],
        "explicit_command_always_asks": {"value": True},
        "javascript": {"on_trusted": "allow", "on_untrusted": "deny"},
        "sessions": {"encrypt_at_rest": True, "persist_domains": {},
                     "max_persisted_domains": 25},
        "extraction": {"max_text_chars": 200_000, "max_links": 500,
                       "max_table_rows": 1_000},
        "blocked": {
            "exact": ["malware.test", "localhost", "127.0.0.1", "0.0.0.0", "::1",
                      "169.254.169.254", "metadata.google.internal"],
            "suffix": [".evil.test", ".internal", ".localhost", ".local"],
            "private_ranges": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                               "169.254.0.0/16", "127.0.0.0/8"],
        },
        "downloads": {"max_file_size_mb": 100, "classify_before_release": True,
                      "require_confirmation_to_move": True,
                      "require_elevated_permission_for": [
                          ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1",
                          ".vbs", ".js", ".jar", ".hta"]},
        "tabs": {"hard_cap": 5, "soft_warning_at": 3, "user_override_allowed": True,
                 "override_requires_separate_confirmation": True},
    }


# -- Playwright doubles -------------------------------------------------


class StubLocator:
    """A Playwright locator that returns whatever the test planted."""

    def __init__(self, owner: "StubPage", selector: str) -> None:
        self._owner = owner
        self._selector = selector

    async def count(self) -> int:
        """Return how many elements the selector matches.

        Returns:
            The count from the page's element map.
        """
        return len(self._owner.elements.get(self._selector, []))

    @property
    def first(self) -> "StubLocator":
        """The first match, as Playwright's property of the same name.

        A property rather than a method on purpose: Playwright's is a property, and a
        stub that made it a method would turn every ``locator.first.count()`` in the
        Body into a confusing AttributeError rather than a pass.

        Returns:
            This locator.
        """
        return self

    async def inner_text(self, timeout: Optional[float] = None) -> str:
        """Return the element's text.

        Args:
            timeout: Ignored; there is nothing to time out against.

        Returns:
            The planted text.
        """
        return self._owner.text_of(self._selector)

    async def get_attribute(self, name: str, timeout: Optional[float] = None) -> Optional[str]:
        """Return an attribute.

        Args:
            name: The attribute.
            timeout: Ignored.

        Returns:
            The planted value, or None.
        """
        return self._owner.attributes.get(self._selector, {}).get(name)

    async def bounding_box(self) -> Optional[Dict[str, float]]:
        """Return the element's box.

        Returns:
            The planted box, or None when the element has none.
        """
        return self._owner.boxes.get(self._selector)

    async def is_visible(self) -> bool:
        """Return whether the element is visible.

        Returns:
            True when a box is planted.
        """
        return self._selector in self._owner.boxes

    async def is_enabled(self) -> bool:
        """Return whether the element is enabled.

        Returns:
            True unless the test disabled it.
        """
        return self._selector not in self._owner.disabled

    async def is_editable(self) -> bool:
        """Return whether the element accepts text.

        Returns:
            True when the test did not mark it readonly.
        """
        return self._selector not in self._owner.readonly

    async def fill(self, value: str, timeout: Optional[float] = None) -> None:
        """Type a value into the element.

        Args:
            value: What was typed.
            timeout: Ignored.

        Returns:
            Nothing. The typed value is recorded on the page.
        """
        self._owner.typed[self._owner.text_of(self._selector)] = value

    async def select_option(self, value: str, timeout: Optional[float] = None) -> None:
        """Choose an option.

        Args:
            value: The option's value.
            timeout: Ignored.

        Returns:
            Nothing.
        """
        self._owner.chosen[self._owner.text_of(self._selector)] = value

    async def click(self, **kwargs: Any) -> None:
        """Click the element.

        Args:
            **kwargs: Ignored.

        Returns:
            Nothing. The click is recorded so a test can prove it happened.
        """
        self._owner.clicks.append(self._owner.text_of(self._selector))

    async def check(self, **kwargs: Any) -> None:
        """Tick a checkbox.

        Args:
            **kwargs: Ignored.

        Returns:
            Nothing.
        """
        self._owner.clicks.append("check:" + self._owner.text_of(self._selector))

    async def press(self, key: str, **kwargs: Any) -> None:
        """Press a key.

        Args:
            key: The key.
            **kwargs: Ignored.

        Returns:
            Nothing.
        """
        self._owner.keys.append(key)

    async def scroll_into_view_if_needed(self, timeout: Optional[float] = None) -> None:
        """Scroll the element into view.

        Args:
            timeout: Ignored.

        Returns:
            Nothing.
        """
        return None

    async def wait_for(self, state: str = "visible", timeout: Optional[float] = None) -> None:
        """Wait for a state.

        Args:
            state: The state wanted.
            timeout: Ignored.

        Raises:
            ElementNotFound: When the selector is absent from the page.
        """
        if self._selector not in self._owner.elements:
            raise ElementNotFound(f"nothing matched {self._selector}")

    async def evaluate(self, script: str = "", arg: Any = None) -> Any:
        """Read this element's metadata.

        Args:
            script: The metadata probe.
            arg: Unused.

        Returns:
            The planted metadata, which is how a test makes an element look like a
            file input or a disabled button.
        """
        return self._owner.metadata.get(self._selector)


class StubMouse:
    """A mouse that records where it was told to go."""

    def __init__(self) -> None:
        self.clicks: List[Any] = []

    async def click(self, x: float, y: float, **kwargs: Any) -> None:
        """Click a point.

        Args:
            x: Horizontal position.
            y: Vertical position.
            **kwargs: Ignored.

        Returns:
            Nothing. The click is recorded so a test can prove it happened.
        """
        self.clicks.append((x, y))

    async def move(self, x: float, y: float, **kwargs: Any) -> None:
        """Move to a point.

        Args:
            x: Horizontal position.
            y: Vertical position.
            **kwargs: Ignored.

        Returns:
            Nothing.
        """
        return None

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        """Scroll.

        Args:
            delta_x: Horizontal scroll.
            delta_y: Vertical scroll.

        Returns:
            Nothing.
        """
        return None


class StubPage:
    """One in-memory page.

    Everything the package asks a page for is answered from planted dictionaries, so
    a test states its page and then asserts against the package's behaviour rather
    than against a real DOM. A field this class does not implement surfaces as an
    AttributeError inside the Body, which is a loud failure rather than a wrong pass.
    """

    def __init__(self, url: str = "about:blank", title: str = "") -> None:
        self.url = url
        self._title = title
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZuzzStub/1.0"
        self.elements: Dict[str, List[Any]] = {}
        self.attributes: Dict[str, Dict[str, str]] = {}
        self.boxes: Dict[str, Dict[str, float]] = {}
        self.disabled: set = set()
        self.readonly: set = set()
        self.file_inputs: set = set()
        self.typed: Dict[str, str] = {}
        self.chosen: Dict[str, str] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}
        self.fields: List[Dict[str, Any]] = []
        self.clicks: List[str] = []
        self.keys: List[str] = []
        self.loaded = True
        self.evaluations: List[str] = []
        self.screenshot_bytes = b"\x89PNG\r\n\x1a\n" + b"stub"
        self.viewport_size: Dict[str, int] = {"width": 1280, "height": 720}
        self.extracts: Dict[str, Any] = {}
        self.goto_error: Optional[Exception] = None
        self.goto_calls: List[str] = []
        self.closed = False
        self.active = True
        self.mouse = StubMouse()
        self.element_probe: Optional[Dict[str, Any]] = None

    # -- helpers the stubs use ------------------------------------------

    def add(
        self,
        selector: str,
        text: str = "",
        box: Optional[Dict[str, float]] = None,
        field_type: str = "text",
        **attributes: str,
    ) -> str:
        """Plant an element.

        Args:
            selector: Its selector.
            text: Its visible text, which is also the handle the package reports.
            box: Its bounding box, if it is visible.
            field_type: Its ``type`` attribute, which drives the refusals — a
                ``file`` input is refused on click, and a ``submit`` button is what
                "submit this form" finds.
            **attributes: Any other attributes.

        Returns:
            The selector, so a test can write ``page.add(...)`` inline.
        """
        self.elements[selector] = [object()]
        self.attributes[selector] = dict(attributes)
        label = text or selector
        identifier = selector.lstrip("#")
        self._texts = getattr(self, "_texts", {})
        self._texts[selector] = label
        tag = "button" if field_type == "submit" else "input"
        # The record shape ``LIST_FIELDS_JS`` produces, because the form filler
        # matches handles by name, id, placeholder, label and aria-label and then
        # rebuilds a selector with ``#id`` — so a planted field whose id does not
        # line up with the element would resolve to something the page does not have.
        self.metadata[selector] = {
            "tag": tag,
            "type": field_type,
            "name": attributes.get("name", ""),
            "id": identifier,
            "placeholder": attributes.get("placeholder", ""),
            "label": label,
            "aria_label": attributes.get("aria-label", ""),
            "text": label,
            "visible": True,
            "disabled": selector in self.disabled,
            "readonly": selector in self.readonly,
        }
        if tag == "input" or field_type in {"text", "password", "email", "search", "file"}:
            self.fields.append(dict(self.metadata[selector], selector=selector))
        if box is not None:
            self.boxes[selector] = box
        return selector

    def text_of(self, selector: str) -> str:
        """Return an element's planted text.

        Args:
            selector: The selector.

        Returns:
            The text, defaulting to the selector.
        """
        return getattr(self, "_texts", {}).get(selector, selector)

    # -- the Playwright surface -----------------------------------------

    def locator(self, selector: str) -> StubLocator:
        """Return a locator.

        Args:
            selector: The selector.

        Returns:
            The locator.
        """
        return StubLocator(self, selector)

    def get_by_text(self, text: str, **kwargs: Any) -> StubLocator:
        """Return a locator by visible text.

        Args:
            text: The text.
            **kwargs: ``exact`` and friends; ignored, since a stub has one match.

        Returns:
            A locator over a synthetic selector.
        """
        return StubLocator(self, "text=" + text)

    def get_by_label(self, label: str, **kwargs: Any) -> StubLocator:
        """Return a locator by label.

        Args:
            label: The label.
            **kwargs: Ignored.

        Returns:
            A locator over a synthetic selector.
        """
        return StubLocator(self, "label=" + label)

    def get_by_placeholder(self, text: str, **kwargs: Any) -> StubLocator:
        """Return a locator by placeholder.

        Args:
            text: The placeholder.
            **kwargs: Ignored.

        Returns:
            A locator over a synthetic selector.
        """
        return StubLocator(self, "ph=" + text)

    def get_by_role(self, role: str, name: str = "", **kwargs: Any) -> StubLocator:
        """Return a locator by role.

        Args:
            role: The ARIA role.
            name: The accessible name.
            **kwargs: ``exact`` and friends; ignored.

        Returns:
            A locator over a synthetic selector.
        """
        return StubLocator(self, "role=" + role + ":" + name)

    def set_default_timeout(self, milliseconds: int) -> None:
        """Set the default timeout.

        Args:
            milliseconds: Ignored.
        """
        return None

    async def goto(self, url: str, **kwargs: Any) -> Any:
        """Navigate.

        Args:
            url: Where to.
            **kwargs: Ignored.

        Returns:
            A response with a 200 status.

        Raises:
            Exception: Whatever the test planted in ``goto_error``.
        """
        self.goto_calls.append(url)
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url
        return type("R", (), {"status": 200})()

    async def title(self) -> str:
        """Return the page title.

        Returns:
            The planted title.
        """
        return self._title

    async def url_(self) -> str:
        """Return the page URL.

        Returns:
            The planted URL.
        """
        return self.url

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        """Run JavaScript, answering the handful of queries the package actually asks.

        Dispatching on the script rather than returning one canned value keeps the
        stub honest: the package asks for the viewport, the user agent and the
        platform separately, and answering all three with one object would make a
        launch look headless or a click land outside a page that "measured" zero.

        Args:
            script: The script.
            arg: Its argument.

        Returns:
            The answer for that shape of query, or whatever the test planted under
            ``extracts["answer"]``.
        """
        self.evaluations.append(script)
        if "innerWidth" in script:
            return {"w": self.viewport_size["width"], "h": self.viewport_size["height"]}
        if "userAgent" in script:
            return self.user_agent
        if "platform" in script:
            return "Win32"
        if "elementFromPoint" in script:
            # The element under a coordinate. One planted answer for the whole page,
            # since a stub has no layout: a test that wants "nothing there" simply
            # leaves it as None, which is what the Body refuses on.
            return self.element_probe
        if "const out = []" in script:
            # The form-field listing. Returning the planted list is what makes a
            # handle resolvable, so a test that fills a form states its fields.
            return list(self.fields)
        return self.extracts.get("answer", {})

    async def screenshot(self, full_page: bool = False, type: str = "png") -> bytes:
        """Return a PNG.

        Args:
            full_page: Ignored.
            type: Ignored.

        Returns:
            The planted bytes.
        """
        return self.screenshot_bytes

    async def wait_for_load_state(self, state: str = "load", **kwargs: Any) -> None:
        """Wait for a load state.

        Args:
            state: The state wanted.
            **kwargs: Ignored.

        Returns:
            Nothing.
        """
        return None

    async def bring_to_front(self) -> None:
        """Focus the page.

        Returns:
            Nothing.
        """
        self.active = True

    async def is_closed(self) -> bool:
        """Return whether the page is closed.

        Returns:
            The recorded state.
        """
        return self.closed

    async def close(self) -> None:
        """Close the page.

        Returns:
            Nothing.
        """
        self.closed = True
        self.active = False


class StubResponse:
    """A response from the stub context's request API."""

    def __init__(self, payload: bytes = b"", content_type: str = "") -> None:
        # Named ``payload`` rather than ``body`` because Playwright's ``body()`` is a
        # method: an attribute of the same name would shadow it and turn every
        # ``await response.body()`` into "bytes is not callable".
        self.payload = payload
        self.headers = {"content-type": content_type}
        if payload:
            self.headers["content-length"] = str(len(payload))

    async def body(self) -> bytes:
        """Return the body.

        Returns:
            The planted bytes.
        """
        return self.payload

    async def dispose(self) -> None:
        """Discard the response.

        Returns:
            Nothing.
        """
        return None


class StubRequest:
    """The context's request API. Fetches are recorded, not performed."""

    def __init__(self) -> None:
        self.fetched: List[str] = []
        self.response = StubResponse(b"%PDF-1.4 stub", "application/pdf")

    async def get(self, url: str, **kwargs: Any) -> StubResponse:
        """Fetch a URL.

        Args:
            url: What to fetch.
            **kwargs: Ignored.

        Returns:
            The planted response, so a test never reaches the network.
        """
        self.fetched.append(url)
        return self.response


class StubContext:
    """A browsing context holding pages."""

    def __init__(self) -> None:
        self.pages: List[StubPage] = []
        self.cookies_added: List[Any] = []
        self.state_written: Optional[Dict[str, Any]] = None
        self.request = StubRequest()

    async def new_page(self) -> StubPage:
        """Open a page.

        Returns:
            A new stub page.
        """
        page = StubPage()
        self.pages.append(page)
        return page

    async def pages(self) -> List[StubPage]:
        """Return the open pages.

        Returns:
            The list. A method, because Playwright's is — several call sites would
            silently treat a list as a coroutine otherwise.
        """
        return list(self.pages)

    async def storage_state(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Return the context's state.

        Args:
            path: Ignored; the stub keeps it in memory so a test cannot write.

        Returns:
            An empty state.
        """
        return {"cookies": [], "origins": []}

    async def add_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        """Add cookies.

        Args:
            cookies: The cookies.

        Returns:
            Nothing.
        """
        self.cookies_added.extend(cookies)

    async def close(self) -> None:
        """Close the context.

        Returns:
            Nothing.
        """
        for page in self.pages:
            page.closed = True


class StubController:
    """A controller with no Playwright behind it.

    Implements the surface :class:`~body.legs.browser_controller.BrowserController`
    exposes to its collaborators. :attr:`launches` and :attr:`closes` record what was
    asked for, which is what lets a test assert that a refusal produced no browser.
    """

    def __init__(self, headless: bool = False, agent: str = "Mozilla/5.0") -> None:
        self.sessions: Dict[str, Any] = {}
        self.launches: List[Dict[str, Any]] = []
        self.closes: List[str] = []
        self.headless = headless
        self.user_data_dir: Optional[Path] = None
        self.allow_headless = False
        self._lock = __import__("asyncio").Lock()
        self._next = 0
        self.agent = agent
        from brain.spinal_cord.kill_switch import KillSwitch

        self.kill_switch = KillSwitch(paths=[], audit=None)

    async def launch(
        self,
        decision: PermissionDecision,
        headless: bool = False,
        persist_domains: Optional[List[str]] = None,
    ) -> str:
        """Pretend to open a browser.

        Args:
            decision: The allowed decision; its category is verified.
            headless: Whether a window was asked for.
            persist_domains: Domains whose state may be kept.

        Returns:
            A session id.

        Raises:
            Exception: When the test asked for a launch failure.
        """
        self._verify(decision, "browse_internet")
        if headless and not self.allow_headless:
            from body.legs.exceptions import BrowserUnavailable

            raise BrowserUnavailable("headless browsing is disabled")
        self.launches.append({"headless": headless,
                              "persist": sorted(persist_domains or ())})
        self._next += 1
        session_id = f"stub-{self._next:03d}"
        context = StubContext()
        page = StubPage()
        context.pages.append(page)
        from body.legs.browser_controller import BrowserSession

        session = BrowserSession(
            session_id=session_id,
            playwright=None,
            browser=None,
            context=context,
            page=page,
            pages=[page],
            headless=bool(headless),
            persist_domains=set(persist_domains or ()),
        )
        self.sessions[session_id] = session
        return session_id

    async def navigate(
        self,
        session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """Pretend to load a URL.

        The decision and the allowlist check have already happened by the time this
        is called — that split is deliberate, and it is why the policy tests above
        can exercise the rules without a browser in the room.

        Args:
            session_id: The session.
            url: The already-approved URL.
            wait_until: Playwright's readiness condition.
            timeout_ms: How long to wait.

        Returns:
            The navigation record.

        Raises:
            Exception: Whatever the test planted in ``goto_error``.
        """
        session = self.get(session_id)
        response = await session.page.goto(url)
        session.record_nav(url, getattr(response, "status", None))
        return {
            "url": url,
            "title": await session.page.title(),
            "status": getattr(response, "status", None),
            "final_url": session.page.url,
            "redirected_to": session.page.url if session.page.url != url else "",
        }

    async def close(self, decision: PermissionDecision, session_id: str = "") -> Dict[str, Any]:
        """Pretend to close a browser.

        Args:
            decision: The allowed decision.
            session_id: Which one.

        Returns:
            What was closed.
        """
        self._verify(decision, "browse_internet")
        target = session_id or next(iter(self.sessions), "")
        if target in self.sessions:
            del self.sessions[target]
            self.closes.append(target)
            return {"closed": True, "session_id": target}
        return {"closed": False, "session_id": target}

    async def close_all(self, decision: PermissionDecision) -> List[str]:
        """Pretend to close every browser.

        Args:
            decision: The allowed decision.

        Returns:
            The ids that were closed.
        """
        closed = list(self.sessions)
        for session_id in closed:
            del self.sessions[session_id]
            self.closes.append(session_id)
        return closed

    def get(self, session_id: str = "") -> Any:
        """Return a session.

        Args:
            session_id: Which one; empty means the only one.

        Returns:
            The session.

        Raises:
            Exception: When there is no such session.
        """
        from body.legs.exceptions import SessionRefused

        if not session_id:
            if not self.sessions:
                raise SessionRefused("no browser session is open")
            return next(iter(self.sessions.values()))
        if session_id not in self.sessions:
            raise SessionRefused(f"no browser session {session_id}")
        return self.sessions[session_id]

    def is_alive(self, session_id: str = "") -> bool:
        """Return whether a session's browser is still connected.

        Synchronous, matching the real controller: it is read by
        :meth:`~body.legs.tab_manager.TabManager.list_tabs`, which is itself a
        synchronous gauge, and an ``async`` stub here would be awaited by nobody.

        Args:
            session_id: Which one.

        Returns:
            True when the session is open.
        """
        if not session_id:
            return bool(self.sessions)
        return session_id in self.sessions

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List the open sessions.

        Returns:
            One record per session.
        """
        return [
            {
                "session_id": session.session_id,
                "headless": session.headless,
                "pages": len(session.pages),
                "created": session.created,
                "last_used": session.last_used,
            }
            for session in self.sessions.values()
        ]

    def _verify(self, decision: PermissionDecision, category: str) -> None:
        """Check that the decision is an allowed one for this category.

        Args:
            decision: The decision.
            category: The category required.

        Returns:
            Nothing.

        Raises:
            Exception: When the decision does not permit it.
        """
        from body.legs.exceptions import DecisionRefused

        if not getattr(decision, "allowed", False):
            raise DecisionRefused(getattr(decision, "reason", "refused"))
        if str(getattr(decision.action, "category", "")) != str(category):
            raise DecisionRefused(
                f"this decision is for {decision.action.category}, not {category}"
            )


# -- decision helpers ---------------------------------------------------


def allowed(category: str, target: str = "") -> PermissionDecision:
    """Build an allowed decision, so a test can drive the Body directly.

    Args:
        category: The action category.
        target: The target the decision is about.

    Returns:
        An allowed decision.
    """
    return PermissionDecision(
        action=Action(category=ActionCategory(category), target=target),
        allowed=True,
        requires_confirmation=False,
        decision="allow",
        reason="test",
        trust_level=5,
    )


# -- fixtures -----------------------------------------------------------


@pytest.fixture
def controller() -> StubController:
    """A controller that opens no browser."""
    return StubController()


@pytest.fixture
def stack(controller: StubController, allowlist: Dict[str, Any], tmp_path: Path) -> BrowserActions:
    """A full stack over the stub controller and an injected allowlist.

    Every path the package would write to is redirected into ``tmp_path``, so a test
    that reaches for quarantine or persisted sessions cannot touch the developer's
    real storage even if a path default were wrong.
    """
    from brain.spinal_cord.kill_switch import KillSwitch
    from body.legs.url_guard import UrlGuard

    switch = KillSwitch(paths=[tmp_path / "stop.flag"], audit=None)
    controller.kill_switch = switch
    guard = UrlGuard(config=allowlist, kill_switch=switch, path=tmp_path / "allow.json")
    actions = BrowserActions(
        controller=controller,
        guard=guard,
        kill_switch=switch,
        session_root=tmp_path / "sessions",
        download_root=tmp_path / "downloads",
    )
    actions.controller.kill_switch = switch
    for collaborator in (actions.tabs, actions.interactor, actions.forms,
                         actions.extractor, actions.downloads, actions.sessions):
        collaborator.kill_switch = switch
    return actions


@pytest.fixture
async def session_id(stack: BrowserActions) -> str:
    """One open session, so tests need not launch for every assertion."""
    return (await stack.open(allowed("browse_internet")))["session_id"]


@pytest.fixture
async def page(stack: BrowserActions, session_id: str) -> StubPage:
    """The stub page behind :func:`session_id`, for planting elements.

    Tests assert against the *package's* behaviour by reading what it asked the page
    to do, so this is the handle a test needs rather than a convenience.
    """
    return stack.controller.get(session_id).page


# -- the URL guard ------------------------------------------------------


class TestUrlGuardPolicy:
    """The allowlist decides what may be reached at all, before the gate is asked.

    ``check`` is the policy layer on its own: it answers about a URL without a
    decision. ``verify`` below is the same policy plus the decision, and is what
    turns these answers into refusals.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ],
    )
    def test_the_scheme_is_blocked(self, stack: BrowserActions, url: str) -> None:
        """A scheme that can read the disk or run code is never navigable."""
        verdict = stack.guard.check(url)
        assert verdict.blocked is True
        assert "scheme" in verdict.reason

    def test_an_exactly_blocked_domain_is_blocked(self, stack: BrowserActions) -> None:
        """A blocked domain is a refusal, not a question."""
        verdict = stack.guard.check("https://malware.test/x")
        assert verdict.blocked is True
        assert verdict.needs_confirmation is False

    def test_a_blocked_suffix_is_blocked(self, stack: BrowserActions) -> None:
        """A suffix rule catches the subdomains a bare domain rule misses."""
        assert stack.guard.check("https://a.b.evil.test/x").blocked is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/admin",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://[::1]/",
            "http://0.0.0.0/",
        ],
    )
    def test_private_and_local_addresses_are_blocked(
        self, stack: BrowserActions, url: str
    ) -> None:
        """Localhost and private ranges are refused whatever the trust level.

        The metadata endpoint on link-local is what an exfiltration attempt looks
        like, and a browser that can reach it is a browser pointed at the user's own
        infrastructure — which is why this is a block and not a prompt.
        """
        verdict = stack.guard.check(url)
        assert verdict.blocked is True
        assert verdict.needs_confirmation is False

    def test_a_preset_is_allowed(self, stack: BrowserActions) -> None:
        """A listed domain passes without asking."""
        verdict = stack.guard.check("https://example.com/a")
        assert verdict.allowed is True
        assert verdict.blocked is False
        assert verdict.needs_confirmation is False

    def test_an_unknown_domain_needs_confirmation(self, stack: BrowserActions) -> None:
        """An unknown but reachable domain is a question, not a refusal."""
        verdict = stack.guard.check("https://unknown.test/page")
        assert verdict.blocked is False
        assert verdict.needs_confirmation is True
        assert verdict.domain == "unknown.test"

    def test_an_explicit_command_asks_even_for_a_preset(
        self, stack: BrowserActions
    ) -> None:
        """A URL the user named is confirmed even when the site is already listed.

        A site being known says nothing about *this page*, and a listed site is
        exactly where a convincing page would be hosted — so the config asks for the
        named case explicitly rather than assuming the site answer covers it.
        """
        verdict = stack.guard.check("https://example.com/a", explicit_user_command=True)
        assert verdict.needs_confirmation is True
        assert verdict.source == "explicit"

    def test_a_malformed_url_is_refused(self, stack: BrowserActions) -> None:
        """A URL that cannot be parsed is blocked, not guessed at."""
        assert stack.guard.check("not a url").blocked is True

    def test_the_domain_is_the_normalised_one(self, stack: BrowserActions) -> None:
        """The confirmation is about the host, not the string the caller typed."""
        verdict = stack.guard.check("https://Unknown.TEST:8443/x?y=1")
        assert verdict.domain == "unknown.test"

    def test_scripting_is_denied_on_an_unapproved_domain(
        self, stack: BrowserActions
    ) -> None:
        """An unapproved page runs with scripting off until the user confirms it.

        Allowing a page's JavaScript before allowing the page would hand the page the
        run of the user's approval, which is the part the allowlist exists to gate.
        """
        verdict = stack.guard.check("https://unknown.test/x")
        assert verdict.scripting in {"deny", "ask"}
        assert stack.guard.check("https://example.com/x").scripting == "allow"

    def test_an_approved_domain_grants_scripting(self, stack: BrowserActions) -> None:
        """The trust the allowlist expresses is not only about fetching."""
        assert stack.guard.check("https://example.com/x").scripting == "allow"


class TestUrlGuardRefusals:
    """The policy becomes an exception, which becomes an HTTP status."""

    def test_a_blocked_scheme_raises_scheme_refused(self, stack: BrowserActions) -> None:
        """A scheme refusal names itself, so the log says which rule fired."""
        with pytest.raises(SchemeRefused):
            stack.guard.verify(allowed("browse_internet", "file:///etc/passwd"),
                               "file:///etc/passwd", "browse_internet")

    def test_a_private_address_raises_private_network_refused(
        self, stack: BrowserActions
    ) -> None:
        """A bare private-range address is told apart from a named block.

        Both are refused, and both are refused before the target is matched — but an
        operator reading the log needs to know whether a rule fired or a page was
        pointed at the user's own network, because those are different failures.
        """
        for url in ("http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.4.2/"):
            with pytest.raises(PrivateNetworkRefused):
                stack.guard.verify(
                    allowed("browse_internet", url), url, "browse_internet"
                )

    def test_a_named_block_is_told_apart_from_a_private_range(
        self, stack: BrowserActions
    ) -> None:
        """The metadata endpoint is a named rule, so it reports as one."""
        url = "http://169.254.169.254/"
        with pytest.raises(BlockedDomainRefused):
            stack.guard.verify(allowed("browse_internet", url), url, "browse_internet")

    def test_a_blocked_domain_raises_blocked_domain_refused(
        self, stack: BrowserActions
    ) -> None:
        """A block rule is a refusal, so no prompt can unlock it."""
        with pytest.raises(BlockedDomainRefused):
            stack.guard.verify(
                allowed("browse_internet", "https://malware.test/x"),
                "https://malware.test/x", "browse_internet",
            )

    def test_an_unknown_domain_raises_domain_confirmation_required(
        self, stack: BrowserActions
    ) -> None:
        """The one refusal that is a question carries the domain to ask about."""
        with pytest.raises(DomainConfirmationRequired) as caught:
            stack.guard.verify(
                allowed("browse_internet", "https://unknown.test/x"),
                "https://unknown.test/x", "browse_internet",
            )
        assert caught.value.domain == "unknown.test"

    def test_a_block_is_checked_before_the_target_matches(
        self, stack: BrowserActions
    ) -> None:
        """A hard block wins even when the decision names a different URL.

        Reporting a replay instead would tell the caller to go and get a different
        permission, when no permission on this machine would help.
        """
        decision = allowed("browse_internet", "https://example.com/")
        with pytest.raises(PrivateNetworkRefused):
            stack.guard.verify(decision, "http://10.0.0.5/", "browse_internet")

    def test_a_decision_for_another_category_is_a_replay(
        self, stack: BrowserActions
    ) -> None:
        """A decision for one action cannot be spent on another."""
        from body.legs.exceptions import DecisionReplayRefused

        with pytest.raises(DecisionReplayRefused):
            stack.guard.verify(
                allowed("web_automation", "https://example.com/"),
                "https://example.com/", "browse_internet",
            )

    def test_a_decision_for_another_host_is_a_replay(
        self, stack: BrowserActions
    ) -> None:
        """An approval for one site is not an approval for a different one."""
        from body.legs.exceptions import DecisionReplayRefused

        with pytest.raises(DecisionReplayRefused):
            stack.guard.verify(
                allowed("browse_internet", "https://example.com/one"),
                "https://duckduckgo.com/two", "browse_internet",
            )

    def test_a_decision_without_a_scheme_still_covers_the_page(
        self, stack: BrowserActions
    ) -> None:
        """"github.com" and "https://github.com/x" are one request to a user.

        The gate decides on what the caller typed, so a decision that named only the
        host must not be rejected for the scheme the Body added on the way in.
        """
        from body.legs.exceptions import DecisionReplayRefused

        stack.guard.verify(
            allowed("browse_internet", "example.com"),
            "https://example.com/x", "browse_internet",
        )
        stack.guard.verify(
            allowed("browse_internet", "https://example.com/one"),
            "https://example.com/two", "browse_internet",
        )

    def test_no_decision_is_refused(self, stack: BrowserActions) -> None:
        """The guard requires a decision, so it cannot be called on its own."""
        from body.legs.exceptions import DecisionRefused

        with pytest.raises(DecisionRefused):
            stack.guard.verify(None, "https://example.com/", "browse_internet")

    def test_a_dry_run_decision_never_browses(self, stack: BrowserActions) -> None:
        """A dry run describes the action; performing it would defeat the preview."""
        from body.legs.exceptions import DryRunRefused

        # Both records carry the flag, and both are set: ``PermissionDecision``
        # mirrors it so the Body cannot be told "this was a dry run" by a caller
        # that only set it on the action, and the frozen dataclasses mean the flag
        # has to be expressed as a different object rather than an assignment.
        base = allowed("browse_internet", "https://example.com/")
        decision = replace(
            base,
            action=replace(base.action, dry_run=True),
            dry_run=True,
        )
        with pytest.raises(DryRunRefused):
            stack.guard.verify(decision, "https://example.com/", "browse_internet")

    def test_the_stop_is_reported_as_the_stop(self, stack: BrowserActions) -> None:
        """A stopped ZUZZ is reported as stopped even when the URL would be refused.

        The two mean different things to whoever reads the log, and collapsing them
        would hide the stop behind a policy line.
        """
        from body.legs.exceptions import KillSwitchRefused

        from brain.spinal_cord.stop_state import SOURCE_UI

        stack.kill_switch.trigger(reason="test", source=SOURCE_UI)
        try:
            with pytest.raises(KillSwitchRefused):
                stack.guard.verify(
                    allowed("browse_internet", "https://example.com/"),
                    "https://example.com/", "browse_internet",
                )
        finally:
            # Clearing a stop takes a typed word. That is the reset policy doing its
            # job, and the tests are not a place to route around it.
            stack.kill_switch.reset("RESET", confirmed=True)


# -- tabs ---------------------------------------------------------------


class TestTabs:
    """Five tabs, a warning at three, and an override that is written down."""

    @pytest.fixture
    async def five(self, stack: BrowserActions, session_id: str) -> None:
        """A session sitting on the hard cap.

        Four opens, not five: Chromium's own first page is adopted into the manager
        when the session opens, so it counts. A cap that did not count the page the
        browser started with would admit a sixth page to a five-tab session.
        """
        for _ in range(4):
            await stack.tabs.open(allowed("web_automation"), session_id)

    async def test_a_new_tab_is_recorded(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """Opening a tab returns a record naming it."""
        record = await stack.tabs.open(
            allowed("web_automation"), session_id, "https://example.com"
        )
        assert record["tab_id"]
        assert record["url"] == "https://example.com"
        assert record["open"] == 2
        assert record["cap"] == 5

    async def test_the_hard_cap_refuses(self, five: None, stack: BrowserActions,
                                        session_id: str) -> None:
        """The sixth tab is refused at the cap."""
        with pytest.raises(TooManyTabsRefused) as caught:
            await stack.tabs.open(allowed("web_automation"), session_id)
        assert caught.value.cap == 5
        assert caught.value.override_possible is True

    async def test_the_refusal_names_the_cost_of_an_override(
        self, five: None, stack: BrowserActions, session_id: str
    ) -> None:
        """The message says what raising the cap would mean, not merely that one exists."""
        with pytest.raises(TooManyTabsRefused) as caught:
            await stack.tabs.open(allowed("web_automation"), session_id)
        assert "override" in str(caught.value).lower()

    async def test_a_confirmed_override_opens_the_sixth(
        self, five: None, stack: BrowserActions, session_id: str
    ) -> None:
        """The override works, but only with a decision that carries it."""
        base = allowed("web_automation")
        override = replace(base, action=replace(base.action, detail={"tab_override": True}))
        record = await stack.tabs.open(override, session_id, override=True)
        assert record["overridden"] is True
        assert record["open"] == 6

    async def test_an_override_without_the_detail_is_refused(
        self, five: None, stack: BrowserActions, session_id: str
    ) -> None:
        """A plain decision cannot be spent on the override.

        The cap exists for a runaway loop, and a loop would not be asking nicely.
        Reusing the decision that wanted the tab would make the cap advisory without
        anyone having been asked.
        """
        with pytest.raises(TooManyTabsRefused):
            await stack.tabs.open(allowed("web_automation"), session_id, override=True)

    async def test_the_soft_warning_never_blocks(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """Three tabs warn; four is still allowed. A warning that blocks is a cap."""
        assert stack.tabs.limits()["soft_warning_at"] == 3
        for _ in range(2):
            record = await stack.tabs.open(allowed("web_automation"), session_id)
        assert record["at_soft_limit"] is True
        assert record["open"] == 3

    async def test_closing_the_last_tab_is_refused(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """A browser with no tabs cannot show the user what it is doing."""
        from body.legs.exceptions import LastTabRefused

        with pytest.raises(LastTabRefused):
            await stack.tabs.close_tab(allowed("web_automation"), session_id, "")

    async def test_closing_one_of_several_works(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """The refusal is about the last tab, not about closing tabs at all."""
        second = await stack.tabs.open(allowed("web_automation"), session_id)
        record = await stack.tabs.close_tab(allowed("web_automation"), session_id,
                                            second["tab_id"])
        assert record["closed"] == second["tab_id"]
        assert record["open"] == 1

    async def test_switching_to_an_unknown_tab_is_refused(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """A tab id that does not exist is a refusal, not a silent first tab."""
        from body.legs.exceptions import TabNotFound

        with pytest.raises(TabNotFound):
            await stack.tabs.switch(allowed("web_automation"), session_id, "nope")

    async def test_listing_tabs_reports_the_cap(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """The list carries the cap, so the UI shows the number the Body will use."""
        listing = stack.tabs.list_tabs(session_id)
        assert listing["hard_cap"] == 5
        assert listing["soft_warning_at"] == 3
        assert listing["open"] == 1


# -- acting on the page -------------------------------------------------


class TestClick:
    """Clicks are the operation most likely to cause the most damage."""

    async def test_a_selector_click_lands(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Clicking a planted element is recorded by the page."""
        page.add("#ok", text="OK")
        record = await stack.click_element(allowed("web_automation"), session_id, "#ok")
        assert record["clicked"] == 1
        assert "OK" in page.clicks

    async def test_visible_text_resolves_to_an_element(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Visible text works as a selector, which is how a user names a button."""
        page.add("text=Sign in", text="Sign in")
        record = await stack.click_element(
            allowed("web_automation"), session_id, "Sign in"
        )
        assert record["clicked"] == 1
        assert "Sign in" in page.clicks

    async def test_a_missing_element_is_refused(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Nothing matched is a refusal, and nothing is clicked."""
        with pytest.raises(ElementNotFound):
            await stack.click_element(allowed("web_automation"), session_id, "#gone")
        assert page.clicks == []

    async def test_a_file_input_is_refused(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Clicking a file chooser would open a dialog ZUZZ cannot see or close."""
        page.add("#upload", text="Upload", field_type="file")
        with pytest.raises((ElementRefused, DecisionRefused, Exception)):
            await stack.click_element(allowed("web_automation"), session_id, "#upload")
        assert page.clicks == []
        assert page.mouse.clicks == []

    async def test_an_off_page_coordinate_is_refused(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """A point outside the viewport cannot be clicked."""
        with pytest.raises((ElementRefused, DecisionRefused, Exception)):
            await stack.click_element(
                allowed("web_automation"), session_id, "",
                coordinates=(99_999, 99_999),
            )
        assert page.clicks == []

    async def test_a_low_confidence_coordinate_is_refused(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Below the configured floor, ZUZZ asks rather than guesses.

        A click on the wrong element is not undoable by the page, and a vision model
        that says 0.3 should not be treated as though it said 0.9.
        """
        page.add("#a", text="A", box={"x": 10, "y": 10, "width": 40, "height": 20})
        with pytest.raises((ElementRefused, DecisionRefused, Exception)):
            await stack.click_element(
                allowed("web_automation"), session_id, "",
                coordinates=(20, 20), confidence=0.05,
            )
        assert page.clicks == []

    async def test_a_confident_coordinate_inside_the_page_lands(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Above the floor, a coordinate is used rather than refused for being one."""
        page.element_probe = {
            "tag": "button", "id": "a", "name": "", "text": "A",
            "x": 10, "y": 10,
        }
        record = await stack.click_element(
            allowed("web_automation"), session_id, "",
            coordinates=(20, 20), confidence=0.99,
        )
        assert record["clicked"] == 1
        assert record["how"] == "coordinates"
        assert page.mouse.clicks == [(20, 20)]

    async def test_a_point_over_nothing_is_refused(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """A confident guess at an empty part of the page is still a refusal.

        Confidence is about the vision model's reading of a screenshot, not about
        whether anything is there; the two are checked separately, and a click that
        lands on whatever happens to be at those coordinates is not the click the
        model described.
        """
        with pytest.raises((ElementRefused, DecisionRefused, Exception)):
            await stack.click_element(
                allowed("web_automation"), session_id, "",
                coordinates=(400, 400), confidence=0.99,
            )
        assert page.mouse.clicks == []


# -- forms --------------------------------------------------------------


class TestForms:
    """Filling is not submitting, and the difference is the whole point."""

    async def test_fields_are_filled(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Each handle is typed into the field it names."""
        page.add("#email", text="Email")
        page.add("#pw", text="Password")
        record = await stack.fill_form(
            allowed("web_automation"), session_id,
            {"Email": "jane@example.com", "Password": "hunter2"},
        )
        assert "Email" in record["filled"]
        assert page.typed["Email"] == "jane@example.com"

    async def test_filling_does_not_submit(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """A plain fill must not press the button.

        This is the assertion most likely to be broken by a well-meaning change that
        makes ``fill`` do "the obvious thing", and it is the change that turns a
        reversible act into an irreversible one.
        """
        page.add("#email", text="Email")
        page.add("button[type=submit]", text="Send")
        await stack.fill_form(allowed("web_automation"), session_id, {"Email": "a@b.c"})
        assert "Send" not in page.clicks
        assert not any(key == "Enter" for key in page.keys)

    async def test_submitting_is_a_separate_act(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """Submitting is asked for on its own and presses the button."""
        page.add("button[type=submit]", text="Send")
        record = await stack.forms.submit(
            allowed("web_automation"), session_id, "button[type=submit]"
        )
        assert record["submitted"] is True
        assert "Send" in page.clicks

    async def test_an_empty_form_is_refused(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """Nothing to fill is a refusal rather than a silent success."""
        from body.legs.exceptions import FormFillRefused

        with pytest.raises((FormFillRefused, DecisionRefused, Exception)):
            await stack.fill_form(allowed("web_automation"), session_id, {})

    async def test_strict_filling_refuses_a_missing_field(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """The strict variant fails rather than half-filling a form.

        Half a login form submitted is worse than none: it produces an error the user
        has to diagnose, on a page they were already signed out of.
        """
        page.add("#email", text="Email")
        with pytest.raises((FormFillRefused, DecisionRefused, Exception)):
            await stack.forms.fill_strict(
                allowed("web_automation"), session_id,
                {"Email": "a@b.c", "Nonexistent": "x"},
            )


# -- extraction ---------------------------------------------------------


class TestExtraction:
    """Reading a page is capped, because an uncapped read is an exfiltration."""

    async def test_text_is_capped(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """A page larger than the cap returns the cap's worth."""
        page.extracts["answer"] = "x" * 50_000
        record = await stack.extract(allowed("browse_internet"), session_id, "text",
                                     limit=100)
        assert len(record["text"]) <= 100
        assert record["truncated"] is True

    async def test_links_are_capped(
        self, stack: BrowserActions, page: StubPage, session_id: str
    ) -> None:
        """A page with nine hundred links does not return nine hundred links."""
        page.extracts["answer"] = [
            {"text": str(i), "href": f"https://example.com/{i}"} for i in range(900)
        ]
        record = await stack.extractor.links(
            allowed("browse_internet"), session_id, limit=10
        )
        assert len(record["links"]) <= 10

    async def test_the_configured_caps_are_reported(
        self, stack: BrowserActions
    ) -> None:
        """The caps are on the gauge, so the UI can say what the limits are."""
        caps = stack.extractor.caps()
        assert caps["max_text_chars"] > 0
        assert caps["max_links"] > 0

    async def test_an_unknown_mode_is_refused(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """A mode the package does not implement is a refusal, not an empty result."""
        with pytest.raises((ValueError, InvalidUrlRefused, Exception)):
            await stack.extract(allowed("browse_internet"), session_id, "exfiltrate")

    async def test_a_selector_that_matches_nothing_is_refused(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """Scoped extraction that found nothing is a refusal."""
        with pytest.raises((ElementNotFound, Exception)):
            await stack.extract(
                allowed("browse_internet"), session_id, "table", "#no-such-table"
            )


# -- downloads ----------------------------------------------------------


class TestDownloads:
    """Two decisions, a holding pen, and executables refused."""

    def test_a_plan_names_the_destination_before_anything_is_fetched(
        self, stack: BrowserActions
    ) -> None:
        """The plan says where the file will land while it is still a plan."""
        plan = stack.downloads.plan("https://example.com/report.pdf")
        assert plan.filename == "report.pdf"
        assert plan.destination.endswith("report.pdf")
        assert "quarantine" in plan.destination or "downloads" in plan.destination

    def test_a_plan_does_not_touch_the_disk(
        self, stack: BrowserActions
    ) -> None:
        """Planning a download creates nothing."""
        stack.downloads.plan("https://example.com/report.pdf")
        assert not stack.downloads.root.exists() or not list(stack.downloads.root.iterdir())

    def test_the_destination_cannot_escape_the_quarantine(
        self, stack: BrowserActions
    ) -> None:
        """A traversal in the file name is flattened, not followed.

        ``../../.ssh/authorized_keys`` as a download name is the shape of an
        overwrite, and the only safe destination is inside the pen.
        """
        plan = stack.downloads.plan(
            "https://example.com/x", filename="../../.ssh/authorized_keys"
        )
        assert plan.destination.startswith(str(stack.downloads.root))

    def test_an_executable_is_classified_by_extension(
        self, stack: BrowserActions
    ) -> None:
        """An ``.exe`` is flagged, and flagged as needing the user's specific ask."""
        verdict = stack.downloads.classify("setup.exe")
        assert verdict["executable"] is True
        assert verdict["requires_elevated"] is True
        assert "extension" in verdict["basis"]

    def test_an_executable_plan_records_the_elevated_request(
        self, stack: BrowserActions
    ) -> None:
        """The plan carries whether the user said they wanted this."""
        assert stack.downloads.plan(
            "https://example.com/report.pdf", elevated=True
        ).elevated_requested is True

    async def test_an_executable_is_refused_when_the_user_did_not_ask(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """An executable arriving from a page is not fetched without a specific ask.

        ``elevated`` is not a permission a caller can grant itself — it is a claim
        that *the user asked for an installer*. With no such claim the fetch is
        refused, and the refusal is here at the fetch rather than at the plan so
        that a dry run can still describe what an executable download would involve.
        """
        url = "https://example.com/setup.exe"
        with pytest.raises(ExecutableDownloadRefused):
            await stack.downloads.fetch(
                allowed("download_files", url), allowed("write_files", url),
                session_id, url,
            )
        assert stack.controller.get(session_id).context.request.fetched == []

    async def test_an_executable_is_fetched_when_the_user_did_ask(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """A user who asked for an installer is not second-guessed.

        The refusal is about the *absence* of a request, so a test that only ever
        exercised the refusal would leave the other half of the rule untested — and
        an over-strict refusal that made installers permanently impossible would look
        exactly like safety while being a bug.
        """
        url = "https://example.com/setup.exe"
        plan = stack.downloads.plan(url, elevated=True)
        record = await stack.downloads.fetch(
            allowed("download_files", url), allowed("write_files", plan.destination),
            session_id, url, plan=plan, elevated=True,
        )
        assert record["classification"]["executable"] is True
        assert stack.controller.get(session_id).context.request.fetched == [url]
        assert record["path"] == plan.destination

    def test_the_quarantine_lists_what_is_held(
        self, stack: BrowserActions
    ) -> None:
        """The gauge names held files and sizes them, and reads none."""
        assert stack.list_downloads() == []

    async def test_the_fetch_decision_alone_does_not_write(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """``download_files`` and ``write_files`` are two acts on two things.

        A confirmation for "fetch this" must not be spendable on "put it at
        C:\\...", so a decision of the wrong category is refused before anything is
        fetched.
        """
        from body.legs.exceptions import DecisionRefused

        url = "https://example.com/a.pdf"
        with pytest.raises((DecisionRefused, Exception)):
            await stack.download_file(
                allowed("download_files", url), allowed("browse_internet", url),
                session_id, url,
            )
        assert stack.list_downloads() == []

    async def test_a_refused_write_leaves_nothing_behind(
        self, stack: BrowserActions, session_id: str
    ) -> None:
        """A refused ``write_files`` must not create the file."""
        url = "https://example.com/a.pdf"
        with pytest.raises((DecisionRefused, Exception)):
            await stack.download_file(
                allowed("download_files", url), allowed("read_files", url),
                session_id, url,
            )
        assert stack.list_downloads() == []


# -- the API flow -------------------------------------------------------


@pytest.fixture
def client(controller: StubController, allowlist: Dict[str, Any], tmp_path: Path):
    """A test client whose browser cannot reach a real one, a real disk or the stop.

    The kill switch is an isolated instance rather than the process-wide singleton,
    because these tests engage the stop to prove it outranks the browser — and the
    singleton would write a stop flag into the developer's real storage and disable
    the running assistant afterwards.

    ``app.state`` is a module-level singleton shared with every other test module, so
    the six attributes replaced here are put back on teardown. Without that, a trust
    level of 5 and a fabricated stop scope outlive the test that set them, and the
    next module to read them — ``test_integration``'s classifier, for one — measures
    a system that only ever existed inside this file. A failing test three files away
    is a much worse report than a slow one.
    """
    from brain.spinal_cord.kill_switch import KillSwitch
    from brain.spinal_cord.stop_scope import StopScope
    from body.legs.url_guard import UrlGuard
    from core.audit import AuditLog
    from face.api.file_confirmations import PendingConfirmations

    replaced = {
        name: getattr(app.state, name, None)
        for name in ("kill_switch", "stop_scope", "legs", "trust", "confirmations")
    }

    switch = KillSwitch(paths=[tmp_path / "stop.flag"], audit=AuditLog(tmp_path / "audit.jsonl"))
    controller.kill_switch = switch
    guard = UrlGuard(config=allowlist, kill_switch=switch, path=tmp_path / "allow.json")
    app.state.kill_switch = switch
    # The StopScope holds the switch too, and the /api/kill-switch/stop route engages
    # *its* switch rather than reading app.state.kill_switch. Swapping only the
    # latter would make the route stop something the browser never hears about —
    # which would turn "the stop outranks the browser" into a test that passes for
    # the wrong reason.
    app.state.stop_scope = StopScope(switch)
    app.state.legs = BrowserActions(
        controller=controller,
        guard=guard,
        kill_switch=switch,
        session_root=tmp_path / "sessions",
        download_root=tmp_path / "downloads",
    )
    app.state.trust = TrustManager(tmp_path / "trust.json")
    app.state.trust.save(5)
    # A fresh confirmation store per test, so an unanswered prompt from one test
    # cannot be answered by another. It is in-memory for the same reason.
    app.state.confirmations = PendingConfirmations()
    try:
        yield TestClient(app)
    finally:
        for name, value in replaced.items():
            setattr(app.state, name, value)


def open_browser(client: TestClient) -> str:
    """Start a browser through the API and return its session id.

    Args:
        client: The test client.

    Returns:
        The session id.
    """
    body = client.post("/api/browser/open", json={}).json()
    return body["result"]["session_id"]


class TestApiGauges:
    """The three reads that are not acts, because they are not about a page."""

    def test_the_gauges_answer_without_a_confirmation(self, client: TestClient) -> None:
        """Status, tabs and the holding pen are gauges.

        A gauge behind a confirmation prompt is a gauge nobody looked at, and these
        are what the user needs in order to decide whether to grant one.
        """
        assert client.get("/api/browser/session-status").status_code == 200
        assert client.get("/api/browser/tabs").status_code == 200
        assert client.get("/api/browser/downloads").status_code == 200

    def test_session_status_reports_the_caps(self, client: TestClient) -> None:
        """The limits are on the gauge, so the UI shows the numbers that will apply."""
        body = client.get("/api/browser/session-status").json()
        assert body["tabs"]["hard_cap"] == 5
        assert body["tabs"]["soft_warning_at"] == 3
        assert body["downloads"]["max_mb"] == 100
        assert body["sessions_persisted"]["encrypted"] is True
        assert body["headless_allowed"] is False

    def test_the_status_reads_no_page(self, client: TestClient) -> None:
        """The gauge reports ZUZZ's own state and nothing from inside a page."""
        body = client.get("/api/browser/session-status").json()
        assert "text" not in body
        assert "cookies" not in body
        assert set(body) == {
            "sessions", "tabs", "open_tabs", "downloads", "quarantine",
            "sessions_persisted", "engines", "allowlist", "extraction",
            "stopped", "headless_allowed",
        }


class TestApiLifecycle:
    """Opening and closing, including the two ways they are refused."""

    def test_opening_a_browser_executes(
        self, client: TestClient, controller: StubController
    ) -> None:
        """A launch at trust level 5 runs and returns the session."""
        response = client.post("/api/browser/open", json={"headless": False})
        assert response.status_code == 200, response.text
        assert controller.launches
        assert response.json()["result"]["session_id"]

    def test_the_response_says_whether_the_browser_is_visible(
        self, client: TestClient
    ) -> None:
        """The session records what the browser reported, not what was asked for.

        A status endpoint that claimed a visible browser which was not one would
        defeat the reason the browser is visible.
        """
        body = client.post("/api/browser/open", json={}).json()
        assert body["result"]["headless"] is False

    def test_headless_is_refused_when_disabled(
        self, client: TestClient, controller: StubController
    ) -> None:
        """A hidden window is refused by the Body, not quietly honoured."""
        response = client.post("/api/browser/open", json={"headless": True})
        assert response.status_code == 503
        assert controller.launches == []

    def test_closing_an_unopened_session_is_a_success(self, client: TestClient) -> None:
        """Closing nothing is not an error; the browser is gone either way.

        A second press of Close should not read as a failure, which is the shape a
        user actually meets.
        """
        response = client.post("/api/browser/close", json={})
        assert response.status_code == 200
        assert response.json()["result"]["closed"] is False

    def test_closing_a_real_session_reports_it(self, client: TestClient) -> None:
        """Closing what is open says so."""
        open_browser(client)
        response = client.post("/api/browser/close", json={})
        assert response.status_code == 200
        assert response.json()["result"]["closed"] is True


class TestApiRefusals:
    """The refusals become statuses, and nothing is reached when they do."""

    def test_an_unknown_domain_asks(self, client: TestClient) -> None:
        """Reachable but unapproved is a 202 carrying the domain."""
        open_browser(client)
        response = client.post(
            "/api/browser/navigate", json={"url": "https://unknown.test/x"}
        )
        assert response.status_code == 202, response.text
        assert response.json()["domain"] == "unknown.test"
        assert response.json()["needs_domain_confirmation"] is True

    def test_a_blocked_domain_is_403_with_no_prompt(self, client: TestClient) -> None:
        """A block rule is a refusal, so there is no dialog that can unlock it."""
        open_browser(client)
        response = client.post(
            "/api/browser/navigate", json={"url": "https://malware.test/x"}
        )
        assert response.status_code == 403
        assert "confirmation_id" not in response.json()

    def test_a_private_address_is_403(self, client: TestClient) -> None:
        """Localhost and link-local are refused at the API too."""
        open_browser(client)
        response = client.post(
            "/api/browser/navigate", json={"url": "http://169.254.169.254/"}
        )
        assert response.status_code == 403

    def test_a_refused_url_is_not_navigated_to(
        self, client: TestClient, controller: StubController
    ) -> None:
        """The page is never asked to go where the guard said it must not."""
        open_browser(client)
        client.post("/api/browser/navigate", json={"url": "https://malware.test/x"})
        assert controller.get("stub-001").page.goto_calls == []

    def test_the_stop_outranks_the_browser(self, client: TestClient) -> None:
        """With the stop engaged, a navigation is 409 and no page is visited."""
        open_browser(client)
        # ``ui``, not a made-up source: the route validates the source against the
        # ones the stop policy knows, so an invented one is a 400 and the stop never
        # happens at all — which would make this test pass for the wrong reason if it
        # asserted anything looser than the 409 below.
        stopped = client.post(
            "/api/kill-switch/stop", json={"reason": "test", "source": "ui"}
        )
        assert stopped.status_code == 200, stopped.text
        response = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        )
        assert response.status_code == 409
        assert controller_page(client).goto_calls == []

    def test_a_dead_session_is_404(self, client: TestClient) -> None:
        """Asking about a session that is not open is a 404, not a crash."""
        response = client.post(
            "/api/browser/navigate",
            json={"session_id": "no-such-session", "url": "https://example.com/"},
        )
        assert response.status_code == 404

    def test_a_tab_over_the_cap_is_409(self, client: TestClient) -> None:
        """The sixth tab is refused with a status that says a limit was hit."""
        session_id = open_browser(client)
        for _ in range(5):
            client.post("/api/browser/new-tab", json={"session_id": session_id})
        response = client.post("/api/browser/new-tab", json={"session_id": session_id})
        assert response.status_code == 409


def controller_page(client: TestClient) -> StubPage:
    """Return the first stub session's page.

    Args:
        client: The test client.

    Returns:
        The page.
    """
    return client.app.state.legs.controller.get("stub-001").page


class TestApiConfirmations:
    """A confirmation is a single-use answer to one question about one thing."""

    @pytest.fixture
    def browsing(self, client: TestClient) -> str:
        """A browser open at trust 5, with the level dropped so browsing asks.

        The order matters and is the point: the launch is routine enough to be
        automatic, and the navigation is the act worth asking about. Dropping the
        level first would make the launch itself the question, and these tests would
        then be testing the wrong prompt.
        """
        session_id = open_browser(client)
        app.state.trust.save(1)
        return session_id

    def test_a_browse_asks_at_low_trust(self, client: TestClient, browsing: str) -> None:
        """The prompt is the flow working, not a failure of it."""
        response = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        )
        assert response.status_code == 202, response.text
        assert response.json()["confirmation_id"]

    def test_the_prompt_carries_the_reason_and_the_level(
        self, client: TestClient, browsing: str
    ) -> None:
        """The user is shown why they are being asked, at what level.

        The keys are flat because this is the shared confirmation payload the Files
        and Desktop popups already render. A nested ``decision`` object would have
        looked tidier and shown an empty reason in the prompt.
        """
        body = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        ).json()
        assert body["reason"]
        assert body["trust_level"] == 1
        assert body["decision"] == "ask"
        assert body["target"] == "https://example.com/"

    def test_a_confirmation_is_single_use(self, client: TestClient, browsing: str) -> None:
        """The same id cannot be spent twice.

        A replayable confirmation is a standing permission, and standing permissions
        are what the whole gate exists to prevent.
        """
        asked = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        )
        confirmation = asked.json()["confirmation_id"]

        answered = client.post(
            "/api/browser/navigate",
            json={"url": "https://example.com/", "confirmed": True,
                  "confirmation_id": confirmation},
        )
        assert answered.status_code == 200, answered.text

        replayed = client.post(
            "/api/browser/navigate",
            json={"url": "https://example.com/", "confirmed": True,
                  "confirmation_id": confirmation},
        )
        assert replayed.status_code == 403

    def test_a_confirmation_cannot_be_spent_on_another_site(
        self, client: TestClient, browsing: str
    ) -> None:
        """An approval for one site is not an approval for a different one.

        A different *path* on the same host is deliberately allowed: the gate decides
        on what the caller typed, and "example.com" and "https://example.com/x" are
        one request as far as the user is concerned. A different host is a different
        request, and that is where a replay must be refused.
        """
        asked = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        )
        spent = client.post(
            "/api/browser/navigate",
            json={"url": "https://duckduckgo.com/somewhere-else", "confirmed": True,
                  "confirmation_id": asked.json()["confirmation_id"]},
        )
        assert spent.status_code == 403

    def test_an_unknown_confirmation_id_is_refused(
        self, client: TestClient, browsing: str
    ) -> None:
        """A made-up id finds nothing, and nothing is browsed."""
        response = client.post(
            "/api/browser/navigate",
            json={"url": "https://example.com/", "confirmed": True,
                  "confirmation_id": "invented"},
        )
        assert response.status_code == 403

    def test_a_dry_run_describes_without_navigating(
        self, client: TestClient, browsing: str
    ) -> None:
        """A preview must not load the page."""
        asked = client.post(
            "/api/browser/navigate", json={"url": "https://example.com/"}
        )
        response = client.post(
            "/api/browser/navigate",
            json={"url": "https://example.com/", "confirmed": True,
                  "confirmation_id": asked.json()["confirmation_id"],
                  "dry_run": True},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "dry_run"
        assert controller_page(client).goto_calls == []


class TestApiDomainApproval:
    """The allowlist's own question, which is not the gate's."""

    def test_approving_a_domain_answers_the_prompt_in_one_round_trip(
        self, client: TestClient
    ) -> None:
        """Ticking "remember this site" approves the domain and does the navigation.

        Two round trips for one question is how a caller gets it half-right, and the
        user is being asked one thing.
        """
        open_browser(client)
        asked = client.post(
            "/api/browser/navigate", json={"url": "https://unknown.test/x"}
        )
        answered = client.post(
            "/api/browser/navigate",
            json={"url": "https://unknown.test/x", "confirmed": True,
                  "confirmation_id": asked.json()["confirmation_id"],
                  "allow_domain": True, "remember_domain": True},
        )
        assert answered.status_code == 200, answered.text
        allowlist = client.get(
            "/api/browser/session-status"
        ).json()["allowlist"]
        assert "unknown.test" in allowlist["user_added"]

    def test_an_allowlist_edit_is_audited_not_gated(self, client: TestClient) -> None:
        """Approving a domain returns 200, because the user is the one asking.

        A Body method that let a caller widen its own allowlist through the gate
        would be a Body method that grants itself permission; the audit record with
        ``by="user"`` is what stands in for the gate here.
        """
        response = client.post(
            "/api/browser/allow-domain", json={"domain": "example.org"}
        )
        assert response.status_code == 200, response.text

    def test_a_blocked_domain_cannot_be_allowed(self, client: TestClient) -> None:
        """There is nothing to approve about a domain that is never navigable."""
        response = client.post(
            "/api/browser/allow-domain", json={"domain": "malware.test"}
        )
        assert response.status_code == 403

    def test_a_forgotten_domain_is_not_on_the_list_again(
        self, client: TestClient
    ) -> None:
        """Forgetting takes the approval back.

        Doing only half of this is how a "forget" leaves a working login behind: the
        allowlist entry goes so the domain asks again, and the state file goes so a
        later approval does not restore the session the user discarded.
        """
        client.post("/api/browser/allow-domain",
                    json={"domain": "unknown.test", "remember": True})
        assert "unknown.test" in client.get(
            "/api/browser/session-status"
        ).json()["allowlist"]["user_added"]

        client.post("/api/browser/forget-domain", json={"domain": "unknown.test"})
        allowlist = client.get(
            "/api/browser/session-status"
        ).json()["allowlist"]
        assert "unknown.test" not in allowlist["user_added"]

    def test_forgetting_also_deletes_the_saved_state(
        self, client: TestClient
    ) -> None:
        """The revocation removes both halves, and says which it removed."""
        client.post("/api/browser/allow-domain",
                    json={"domain": "unknown.test", "persist": True})
        response = client.post("/api/browser/forget-domain", json={"domain": "unknown.test"})
        assert response.status_code == 200, response.text
        record = response.json()["result"]
        assert record["state_deleted"] in (True, False)
        assert "allowlist" in record["removed"]


class TestApiBadRequests:
    """A malformed request is a 400, caught before anything is decided."""

    def test_a_click_with_nothing_to_aim_at_is_400(self, client: TestClient) -> None:
        """A click needs a selector, some text, or a point."""
        open_browser(client)
        assert client.post("/api/browser/click", json={}).status_code == 400

    def test_a_fill_with_no_fields_is_400(self, client: TestClient) -> None:
        """An empty form body is a bad request, not an empty success."""
        open_browser(client)
        assert client.post(
            "/api/browser/fill-form", json={"form_data": {}}
        ).status_code == 400

    def test_search_needs_a_query(self, client: TestClient) -> None:
        """Searching for nothing is a bad request."""
        assert client.post("/api/browser/search", json={"query": "  "}).status_code == 400

    def test_an_unknown_engine_is_400(self, client: TestClient) -> None:
        """An engine the package does not have is a bad request."""
        response = client.post(
            "/api/browser/search", json={"query": "kittens", "engine": "altavista"}
        )
        assert response.status_code == 400


class TestApiSearch:
    """A search is gated, and the query is the user's own."""

    def test_the_query_is_not_written_to_the_audit_log(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """What someone searches for is theirs, and it is not in the log.

        The query is the one part of a search that belongs to the user rather than to
        the page, so the log records that a search happened and not what for.
        """
        open_browser(client)
        client.post("/api/browser/search", json={"query": "my-diagnosis-secret"})
        logs = list(tmp_path.glob("*.jsonl"))
        for log in logs:
            assert "my-diagnosis-secret" not in log.read_text(encoding="utf-8")

    def test_the_status_names_which_engines_are_reachable(
        self, client: TestClient
    ) -> None:
        """An engine the user has not approved is visible as unapproved.

        Otherwise the first search to a new engine is a surprise, and the user cannot
        tell in advance which one will ask.
        """
        engines = client.get("/api/browser/session-status").json()["engines"]
        assert engines["default"] == "duckduckgo"
        assert engines["engines"]["duckduckgo"]["available"] is True
        assert engines["engines"]["google"]["blocked"] is True
        assert engines["engines"]["bing"]["blocked"] is True


class TestThePage:
    """The page exists, and every control on it points at a real route."""

    def test_the_page_is_served(self) -> None:
        """There is a browser page for the user to open."""
        assert (FRONTEND / "pages" / "browser.html").is_file()
        assert (FRONTEND / "static" / "js" / "browser.js").is_file()

    def test_every_control_maps_to_a_real_route(self) -> None:
        """No button on the page points at an endpoint that does not exist.

        A control wired to a 404 is worse than no control: it looks like a feature
        and silently does nothing, which is how the Desktop page's Stop button came
        to be posting into a route that was never registered.
        """
        script = (FRONTEND / "static" / "js" / "browser.js").read_text(encoding="utf-8")
        paths = registered_paths(router)
        assert paths, "the browser router should serve something"
        for endpoint in set(re.findall(r"/api/browser/[a-z-]+", script)):
            assert endpoint in paths, endpoint

    def test_every_bound_control_has_an_element(self) -> None:
        """Every static ``id`` the script binds actually exists in the page.

        The inverse of the route check, and the same failure mode in the other
        direction: a handler bound to a button that was renamed is a silently dead
        control. Ids built at runtime — ``on("op-" + mode, …)`` — are skipped, since
        there is no single id to look for.
        """
        script = (FRONTEND / "static" / "js" / "browser.js").read_text(encoding="utf-8")
        page = (FRONTEND / "pages" / "browser.html").read_text(encoding="utf-8")
        bound = {
            element_id
            for element_id in re.findall(r'(?:on|\$)\("([\w-]+)"', script)
            if not element_id.endswith("-")
        }
        assert bound, "the page should bind some controls"
        for element_id in bound:
            assert f'id="{element_id}"' in page, element_id

    def test_the_page_explains_the_two_decision_download(self) -> None:
        """The page says a download is two decisions, because it is."""
        page = (FRONTEND / "pages" / "browser.html").read_text(encoding="utf-8")
        assert "two decisions" in page.lower()
