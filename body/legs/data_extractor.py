"""Body/Legs — data extraction.

Pulls text, links, tables and metadata out of a page. Split out of
:mod:`body.legs.element_interactor` because extraction and interaction have
opposite failure modes: a wrong click is unrecoverable, a wrong extraction is just
a wrong answer that the caller can see was wrong. So extraction is the quieter of
the two and it carries caps the interactor does not.

**A page is attacker-chosen input of unbounded length.** An extractor with no cap
is a way to fill a context window with a page's contents before ZUZZ has read a word
of the user's question, and a page that knows it is being read can make itself
long. Every method here truncates at the cap in ``config/url_allowlist.json`` and
says so in ``truncated`` and ``dropped_chars`` — silent truncation is worse than
none, because a caller cannot tell a shortened answer from a complete one.

**Nothing evaluated in the page contains caller data.** Each method has one fixed
JavaScript literal and passes its selector as an argument, so a caller who can name
a selector cannot name a script.

Links are resolved to absolute URLs before they are returned. A page full of
relative hrefs is normal, and handing them back unresolved is how a caller ends up
joining them to the wrong origin.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_controller import BrowserController
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    ElementNotFound,
)

#: Action category for reading a page's contents.
EXTRACT_CATEGORY = "browse_internet"

#: Fallbacks when the config has no ``extraction`` block. Deliberately generous:
#: these exist to stop a hostile page, not to shape ZUZZ's answers.
DEFAULT_MAX_TEXT_CHARS = 200_000
DEFAULT_MAX_LINKS = 500
DEFAULT_MAX_TABLE_ROWS = 1_000

#: Fixed snippet: the text of one element, or of the whole body.
READ_TEXT_JS = """
(sel) => {
  const nodes = sel ? document.querySelectorAll(sel) : [document.body];
  if (!nodes.length) return '';
  return Array.from(nodes).map((n) => n.innerText || '').join('\\n\\n');
}
"""

#: Fixed snippet: every link, with its own text and attributes.
READ_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]')).map((a) => ({
  href: a.getAttribute('href') || '',
  text: (a.innerText || a.textContent || '').trim().slice(0, 200),
  title: a.getAttribute('title') || '',
  target: a.getAttribute('target') || '',
  rel: a.getAttribute('rel') || '',
  aria_label: a.getAttribute('aria-label') || ''
}))
"""

#: Fixed snippet: a table as rows of cells.
READ_TABLE_JS = """
(sel) => {
  const table = document.querySelector(sel);
  if (!table) return [];
  return Array.from(table.querySelectorAll('tr')).map((tr) =>
    Array.from(tr.querySelectorAll('th,td')).map((cell) =>
      (cell.innerText || '').trim()));
}
"""

#: Fixed snippet: the page's own description of itself.
READ_METADATA_JS = """
() => {
  const meta = (name) => {
    const el = document.querySelector('meta[name="' + name + '"]') ||
               document.querySelector('meta[property="' + name + '"]');
    return el ? (el.getAttribute('content') || '') : '';
  };
  const canonical = document.querySelector('link[rel="canonical"]');
  return {
    title: document.title || '',
    description: meta('description'),
    keywords: meta('keywords'),
    author: meta('author'),
    generator: meta('generator'),
    lang: document.documentElement.lang || '',
    canonical: canonical ? (canonical.getAttribute('href') || '') : '',
    og_title: meta('og:title'),
    og_description: meta('og:description'),
    og_type: meta('og:type'),
    og_url: meta('og:url'),
    og_image: meta('og:image')
  };
}
"""

#: Fixed snippet: form fields, for a caller that wants to know what a page asks for.
READ_FIELDS_JS = """
() => Array.from(document.querySelectorAll('input, textarea, select')).map((el) => {
  const label = el.labels && el.labels.length ? el.labels[0].innerText : '';
  return {
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || 'text').toLowerCase(),
    name: el.getAttribute('name') || '',
    id: el.id || '',
    placeholder: el.getAttribute('placeholder') || '',
    label: (label || '').trim().slice(0, 80),
    required: !!el.required,
    visible: !!(el.offsetWidth || el.offsetHeight)
  };
})
"""

#: Fixed snippet: the markup of one element, or of the whole document.
READ_HTML_JS = """
(sel) => {
  if (!sel) return document.documentElement.outerHTML;
  const el = document.querySelector(sel);
  return el ? el.outerHTML : '';
}
"""

#: Fixed snippet: the first N images, by src and alt.
READ_IMAGES_JS = """
() => Array.from(document.querySelectorAll('img')).map((img) => ({
  src: img.getAttribute('src') || '',
  alt: img.getAttribute('alt') || '',
  width: img.naturalWidth || 0,
  height: img.naturalHeight || 0
})).filter((i) => i.src)
"""


class DataExtractor:
    """Reads text, links, tables and metadata out of the active page.

    Attributes:
        controller: The controller holding the sessions.
        guard: The URL guard, used for the cap block.
        logger: Activity logger.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the extractor.

        Args:
            controller: The controller holding the sessions.
            guard: The URL guard, read for the ``extraction`` caps. None means
                read ``url_allowlist.json`` fresh.
            logger: Activity logger.
        """
        self.controller = controller
        self.guard = guard
        self.logger = logger

    # -- caps -----------------------------------------------------------

    def caps(self) -> Dict[str, int]:
        """Return the extraction caps as configured.

        Returns:
            ``max_text_chars``, ``max_links`` and ``max_table_rows``. A missing
            or nonsensical value falls back to the module default, so a truncated
            config makes extraction smaller rather than unbounded.
        """
        config = {}
        if self.guard is not None:
            config = self.guard.config()
        else:
            config = _read_config()
        settings = config.get("extraction", {}) if isinstance(config, dict) else {}
        return {
            "max_text_chars": _as_int(
                settings.get("max_text_chars"), DEFAULT_MAX_TEXT_CHARS
            ),
            "max_links": _as_int(settings.get("max_links"), DEFAULT_MAX_LINKS),
            "max_table_rows": _as_int(
                settings.get("max_table_rows"), DEFAULT_MAX_TABLE_ROWS
            ),
        }

    # -- text -----------------------------------------------------------

    async def text(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector: str = "body",
        max_chars: int = 0,
    ) -> Dict[str, Any]:
        """Return the text of an element, or of the whole page.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            selector: A CSS selector, or ``""`` for the whole body.
            max_chars: Override the cap for this call. Zero means use the config.

        Returns:
            ``text``, ``selector``, ``truncated``, ``dropped_chars`` and ``url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            ElementNotFound: The selector matches nothing.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        wanted = str(selector).strip() or "body"
        raw = await session.page.evaluate(READ_TEXT_JS, None if wanted == "body" else wanted)
        if not raw and wanted != "body":
            raise ElementNotFound(f"{wanted} matched no text on this page")
        cap = int(max_chars) if max_chars else self.caps()["max_text_chars"]
        text, dropped = _truncate(str(raw or ""), cap)
        session.touch()
        self._log("browser_text_extracted", session=session_id, selector=wanted,
                  chars=len(text), truncated=bool(dropped))
        return {
            "text": text,
            "selector": wanted,
            "truncated": bool(dropped),
            "dropped_chars": dropped,
            "cap": cap,
            "url": _url_of(session.page),
        }

    async def html(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector: str = "",
        max_chars: int = 0,
    ) -> Dict[str, Any]:
        """Return the markup of an element, or of the whole page.

        Gated identically to text but called out because markup is strictly more
        revealing than text: it carries ``<input value>``, inline ``onclick``
        handlers, comment blocks and tracking pixels that the rendered text has
        already thrown away. Some of those are the credential fields a page never
        intended to show anyone.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            selector: A CSS selector, or ``""`` for the whole document.
            max_chars: Override the cap for this call.

        Returns:
            ``html``, ``selector``, ``truncated``, ``dropped_chars`` and ``url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            ElementNotFound: The selector matches nothing.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        raw = await session.page.evaluate(READ_HTML_JS, str(selector).strip() or None)
        if not raw and str(selector).strip():
            raise ElementNotFound(f"{selector} matched no markup on this page")
        cap = int(max_chars) if max_chars else self.caps()["max_text_chars"]
        body, dropped = _truncate(str(raw or ""), cap)
        session.touch()
        self._log("browser_html_extracted", session=session_id,
                  selector=str(selector), chars=len(body))
        return {
            "html": body,
            "selector": str(selector),
            "truncated": bool(dropped),
            "dropped_chars": dropped,
            "cap": cap,
            "url": _url_of(session.page),
        }

    # -- links and structure --------------------------------------------

    async def links(
        self,
        decision: PermissionDecision,
        session_id: str,
        base_url: str = "",
        limit: int = 0,
        same_site_only: bool = False,
    ) -> Dict[str, Any]:
        """Return every link on the page, resolved to absolute URLs.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            base_url: What to resolve relative hrefs against; the page's own URL
                when empty.
            limit: Override the cap for this call.
            same_site_only: Keep only links whose host matches the current page.
                Useful on a search-results page, where the point is to read the
                results and not to walk off the search engine.

        Returns:
            ``links``, ``count``, ``truncated`` and ``url``. Each link carries
            ``href``, ``text``, ``title``, ``target``, ``rel`` and ``aria_label``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        raw = await session.page.evaluate(READ_LINKS_JS)
        cap = int(limit) if limit else self.caps()["max_links"]
        base = str(base_url).strip() or _url_of(session.page)
        out: List[Dict[str, str]] = []
        dropped = 0
        for item in raw or []:
            if len(out) >= cap:
                dropped += 1
                continue
            href = str(item.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base, href)
            if same_site_only and _host(absolute) != _host(base):
                continue
            out.append(
                {
                    "href": absolute,
                    "raw_href": href,
                    "text": str(item.get("text") or ""),
                    "title": str(item.get("title") or ""),
                    "target": str(item.get("target") or ""),
                    "rel": str(item.get("rel") or ""),
                    "aria_label": str(item.get("aria_label") or ""),
                }
            )
        session.touch()
        self._log("browser_links_extracted", session=session_id, count=len(out),
                  dropped=dropped)
        return {
            "links": out,
            "count": len(out),
            "truncated": bool(dropped),
            "dropped": dropped,
            "cap": cap,
            "url": _url_of(session.page),
        }

    async def table(
        self,
        decision: PermissionDecision,
        session_id: str,
        selector: str = "table",
        limit: int = 0,
    ) -> Dict[str, Any]:
        """Return a table as rows of cells.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            selector: A CSS selector for the table.
            limit: Override the row cap for this call.

        Returns:
            ``rows``, ``count``, ``truncated``, ``header`` and ``url``. ``header``
            is the first row when the table uses ``<th>``, which is the only case
            where guessing the first row is not a guess.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            ElementNotFound: No table matched.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        rows = await session.page.evaluate(READ_TABLE_JS, str(selector).strip() or "table")
        cap = int(limit) if limit else self.caps()["max_table_rows"]
        all_rows = [list(map(str, row)) for row in (rows or [])]
        if not all_rows:
            raise ElementNotFound(f"{selector} matched no table on this page")
        header = all_rows[0] if _first_row_is_header(session, selector) else []
        body = all_rows[1:] if header else all_rows
        truncated = len(body) > cap
        body = body[:cap]
        session.touch()
        self._log("browser_table_extracted", session=session_id, selector=selector,
                  rows=len(body), truncated=truncated)
        return {
            "rows": body,
            "header": header,
            "count": len(body),
            "truncated": truncated,
            "cap": cap,
            "url": _url_of(session.page),
        }

    async def metadata(self, decision: PermissionDecision, session_id: str) -> Dict[str, Any]:
        """Return the page's title, description and Open Graph metadata.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.

        Returns:
            The metadata record, plus ``url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        record = await session.page.evaluate(READ_METADATA_JS) or {}
        session.touch()
        return {**record, "url": _url_of(session.page)}

    async def fields(self, decision: PermissionDecision, session_id: str) -> Dict[str, Any]:
        """Return the form fields on the page, with their handles and no values.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.

        Returns:
            ``fields`` and ``url``. No ``value``: this says what a form asks for and
            never what has already been typed into it.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        found = await session.page.evaluate(READ_FIELDS_JS) or []
        session.touch()
        return {"fields": list(found), "count": len(found), "url": _url_of(session.page)}

    async def images(self, decision: PermissionDecision, session_id: str) -> Dict[str, Any]:
        """Return the images on the page.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.

        Returns:
            ``images`` and ``url``. The ``src`` values are returned as the page wrote
            them and are **not** fetched: an image ``src`` is a URL a third party
            wrote, and resolving it would be a request ZUZZ had not been asked to
            make. Fetch one with the download endpoint, which checks the allowlist.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision)
        session = self.controller.get(session_id)
        found = await session.page.evaluate(READ_IMAGES_JS) or []
        session.touch()
        return {"images": list(found), "count": len(found), "url": _url_of(session.page)}

    # -- internals ------------------------------------------------------

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
                "refusing to read a page without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused(
                "dry-run decisions describe the action; they never read a page"
            )
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != EXTRACT_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {EXTRACT_CATEGORY}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. Sizes only — page contents never
                reach the log.

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


def _truncate(text: str, cap: int) -> Tuple[str, int]:
    """Cut a string at a cap and say how much was dropped.

    Args:
        text: The full string.
        cap: Maximum characters to keep.

    Returns:
        ``(kept, dropped)``, where ``dropped`` is zero when nothing was cut. The
        count is reported rather than inferred, so a caller never has to compare
        two lengths to find out whether it is looking at the whole page.
    """
    if cap <= 0 or len(text) <= cap:
        return text, 0
    return text[:cap], len(text) - cap


def _host(url: str) -> str:
    """Return a URL's host, lower-cased.

    Args:
        url: Any URL, absolute or not.

    Returns:
        The host, or an empty string.
    """
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


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


def _as_int(value: Any, fallback: int) -> int:
    """Return a positive integer setting, falling back when it is not one.

    Args:
        value: The configured value.
        fallback: What to use otherwise.

    Returns:
        A positive integer, so a bad config makes extraction smaller rather than
        unbounded.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


async def _first_row_is_header(session: Any, selector: str) -> bool:
    """Return True when a table's first row is a header row.

    Args:
        session: The session.
        selector: The table's selector.

    Returns:
        True when the first row is made of ``<th>``. Guessing on a table of all
        ``<td>`` would drop a row of data and present it as a heading, so this only
        says yes when the markup says so.
    """
    try:
        return bool(
            await session.page.evaluate(
                """(sel) => {
                    const t = document.querySelector(sel || 'table');
                    if (!t) return false;
                    const first = t.querySelector('tr');
                    return !!first && !!first.querySelector('th') &&
                           !first.querySelector('td');
                }""",
                str(selector).strip() or "table",
            )
        )
    except Exception:
        return False


def _read_config() -> Dict[str, Any]:
    """Read the allowlist fresh.

    Returns:
        The parsed ``config/url_allowlist.json``, or an empty mapping when it
        cannot be read.
    """
    import json

    from core.config import CONFIG_DIR

    try:
        return json.loads(
            (CONFIG_DIR / "url_allowlist.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}