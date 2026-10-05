"""Body/Legs — web search.

Three engines, and the choice between them is a safety decision rather than a
quality one. ``google.com`` and ``duckduckgo.com`` are both presets in
:mod:`config.url_allowlist.json`, so a search does not prompt on the first use; a
third engine is refused unless the user has approved its domain, because a search
engine ZUZZ was handed a query by is a third party with the query.

**No search API key, ever.** There is no API key here on purpose. A key would mean
an account, which would mean a quota, which would mean ZUZZ holding a credential
that a leaked query log could be traced back through. Searching is a page load, the
same as opening any other page, and it inherits exactly the same allowlist check.

Results are parsed per engine with a fixed snippet, and fall back to "every link on
the page that is not the search engine's own navigation" when the layout has
changed. That fallback is honest about itself: the results carry
``parser="fallback"`` so a caller can tell a real result list from a guess.

Approved answer Q7's OCR fallback is *not* used here, and the reason is worth
stating: OCR is for reading pixels ZUZZ cannot otherwise read, and a search results
page is DOM. Running OCR over a page whose text is already selectable would be a
model load and a chance to misread a number for no gain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlsplit

from brain.prefrontal_cortex.permission_decision import PermissionDecision

from body.legs.browser_controller import BrowserController
from body.legs.data_extractor import DataExtractor
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    InvalidUrlRefused,
    NavigationFailed,
)

#: Action category for a search. ``browse_internet``: a search is reading, and the
#: click-through to a result is a separate ``web_automation`` decision like any
#: other navigation.
SEARCH_CATEGORY = "browse_internet"

#: How many results to return by default. Ten is what a search engine shows above
#: the fold, so asking for more means scrolling rather than reading, and scrolling
#: triggers the lazy-load traffic the extractor is not allowed to cause.
DEFAULT_RESULTS = 10

#: Upper bound on results for one call.
MAX_RESULTS = 50

#: The engines, their query URLs and the domains that must be reachable for each.
#: The domain is checked against the allowlist like any other URL: naming an engine
#: in a request is not permission to reach it.
ENGINES: Dict[str, Dict[str, str]] = {
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q={query}",
        "domain": "duckduckgo.com",
        "parser": "ddg",
    },
    "google": {
        "url": "https://www.google.com/search?q={query}&num={count}",
        "domain": "google.com",
        "parser": "google",
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}&count={count}",
        "domain": "bing.com",
        "parser": "bing",
    },
}

#: Default engine. DuckDuckGo rather than Google because it is a preset listed as
#: ``search`` with no consent interstitial on this path, and because a page ZUZZ
#: parses the results of is a page it should be able to read without a script
#: challenge in the way.
DEFAULT_ENGINE = "duckduckgo"

#: Fixed snippets, one per parser plus the fallback. None contains caller data.
PARSE_JS = {
    "ddg": """
() => Array.from(document.querySelectorAll('[data-testid="result"]'))
  .map((r) => {
    const a = r.querySelector('a[href]');
    const snip = r.querySelector('[data-result="snippet"]');
    return a ? {href: a.href, title: (a.innerText || '').trim(),
                snippet: snip ? (snip.innerText || '').trim() : ''} : null;
  }).filter(Boolean)
""",
    "google": """
() => Array.from(document.querySelectorAll('div.MjjYud, div.g, div.tF2Cxc'))
  .map((r) => {
    const a = r.querySelector('a[href]');
    const h3 = r.querySelector('h3');
    if (!a || !h3) return null;
    return {href: a.href, title: (h3.innerText || '').trim(),
            snippet: (r.innerText || '').trim().slice(0, 400)};
  }).filter(Boolean)
""",
    "bing": """
() => Array.from(document.querySelectorAll('li.b_algo'))
  .map((r) => {
    const a = r.querySelector('h2 a[href]');
    const snip = r.querySelector('.b_caption p, .b_lineclamp2, .b_algoSlug');
    return a ? {href: a.href, title: (a.innerText || '').trim(),
                snippet: snip ? (snip.innerText || '').trim() : ''} : null;
  }).filter(Boolean)
""",
    "fallback": """
() => {
  const host = location.hostname.replace(/^www\\./, '');
  const skip = /^(duckduckgo|google|bing)\\./;
  return Array.from(document.querySelectorAll('a[href^="http"]'))
    .map((a) => ({href: a.href, title: (a.innerText || '').trim().slice(0, 160),
                  snippet: ''}))
    .filter((r) => r.title.length > 3 &&
                   !skip.test(r.href.replace(/^https?:\\/\\//, '').split('/')[0]) &&
                   r.href.split('/')[2].replace(/^www\\./, '') !== host);
}
""",
}


@dataclass
class SearchResult:
    """One result.

    Attributes:
        rank: 1-based position in the returned list.
        title: The result's own text.
        url: Absolute URL.
        snippet: Whatever description the engine showed.
        engine: Which engine produced it.
        parser: ``ddg``, ``google``, ``bing`` or ``fallback``.
    """

    rank: int
    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    parser: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form.

        Returns:
            Every attribute as a plain dict.
        """
        return {
            "rank": self.rank,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "parser": self.parser,
        }


class WebSearch:
    """Runs a search and returns the results as data.

    Attributes:
        controller: The controller holding the sessions.
        guard: The URL guard, which decides whether the engine is reachable at all.
        extractor: The data extractor, used for the fallback parser.
        logger: Activity logger.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        extractor: Optional[DataExtractor] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the search helper.

        Args:
            controller: The controller holding the sessions.
            guard: The URL guard, consulted for the engine's domain.
            extractor: The data extractor; built from ``controller`` when None.
            logger: Activity logger.
        """
        self.controller = controller
        self.guard = guard
        self.extractor = extractor or DataExtractor(controller, guard, logger)
        self.logger = logger

    def engines(self) -> Dict[str, Any]:
        """Return the supported engines and whether each is reachable.

        Not gated; a gauge. Reachability here means "would the allowlist ask",
        which is exactly what a user deciding which engine to pick needs to know.

        Returns:
            One record per engine: ``url``, ``domain``, ``available`` and
            ``blocked``. ``available`` means the domain is on a list; it does not
            mean a request has been made.
        """
        out: Dict[str, Any] = {}
        for name, spec in ENGINES.items():
            allowed = True
            reason = ""
            if self.guard is not None:
                verdict = self.guard.check(f"https://{spec['domain']}")
                allowed = verdict.allowed
                reason = verdict.reason
            out[name] = {
                "url": spec["url"].split("{query}")[0],
                "domain": spec["domain"],
                "available": bool(allowed),
                "blocked": not allowed,
                "reason": reason,
            }
        return {"default": DEFAULT_ENGINE, "engines": out}

    async def search(
        self,
        decision: PermissionDecision,
        session_id: str,
        query: str,
        engine: str = DEFAULT_ENGINE,
        count: int = DEFAULT_RESULTS,
    ) -> Dict[str, Any]:
        """Search and return the results.

        Args:
            decision: An allowed ``browse_internet`` decision whose target is the
                search URL the guard approved. Built by
                :meth:`~body.legs.browser_actions.BrowserActions.search_web`, which
                checks the allowlist first — naming an engine here is a request to
                reach it, not permission to.
            session_id: The session.
            query: What to search for.
            engine: One of :data:`ENGINES`, or the default.
            count: How many results to return, 1-50.

        Returns:
            ``results``, ``query``, ``engine``, ``parser``, ``search_url`` and
            ``landing_url``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            InvalidUrlRefused: The engine name is not one this module knows.
            NavigationFailed: The search page did not load.
        """
        self._verify(decision)
        text = str(query).strip()
        if not text:
            raise InvalidUrlRefused("no search query was given")
        name = str(engine or DEFAULT_ENGINE).strip().lower()
        if name not in ENGINES:
            raise InvalidUrlRefused(
                f"{engine!r} is not a search engine ZUZZ knows; use one of "
                + ", ".join(sorted(ENGINES))
            )
        wanted = max(1, min(int(count or DEFAULT_RESULTS), MAX_RESULTS))
        spec = ENGINES[name]
        url = spec["url"].format(query=quote_plus(text), count=wanted)
        landing = await self.controller.navigate(session_id, url)
        results, parser = await self._parse(session_id, name, spec["parser"])
        self._log(
            "browser_searched",
            session=session_id,
            engine=name,
            parser=parser,
            results=len(results),
            query_length=len(text),
        )
        return {
            "results": [item.to_dict() for item in results],
            "count": len(results),
            "query": text,
            "engine": name,
            "parser": parser,
            "search_url": url,
            "landing_url": landing.get("final_url", url),
            "blocked": landing.get("status") is not None
            and int(landing.get("status") or 0) >= 400,
        }

    # -- internals ------------------------------------------------------

    async def _parse(
        self, session_id: str, engine: str, parser: str
    ) -> tuple:
        """Parse a search results page, falling back when the layout has moved.

        Args:
            session_id: The session.
            engine: The engine name, for the result records.
            parser: The parser to try first.

        Returns:
            ``(results, parser_used)``. The parser that actually ran is returned so
            the caller can tell a parsed result list from a link dump: a fallback
            result set may include a site's own navigation, and a caller quoting
            it should know that.
        """
        session = self.controller.get(session_id)
        for candidate in (parser, "fallback"):
            try:
                raw = await session.page.evaluate(PARSE_JS[candidate])
            except Exception:
                raw = None
            results = _to_results(raw, engine, candidate)
            if results:
                return results, candidate
        return [], "none"

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
            raise DecisionRefused("refusing to search without a PermissionDecision")
        if decision.dry_run:
            raise DryRunRefused("dry-run decisions describe the action; they never search")
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != SEARCH_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {SEARCH_CATEGORY}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. The **query is not logged**. A search
                is the most revealing thing a user types into any program, and the
                activity log is a file an operator is guaranteed to read.

        Returns:
            Nothing.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("legs", action, **fields)
        except Exception:
            pass


def _to_results(raw: Any, engine: str, parser: str) -> List[SearchResult]:
    """Turn a page's parsed anchors into result records.

    Args:
        raw: What the page snippet returned.
        engine: The engine name.
        parser: The parser that ran.

    Returns:
        Ranked results, deduplicated by URL and without the search engine's own
        domain. Both filters are because a link dump contains the engine's
        navigation — "Images", "Videos", "Sign in" — and a result list that opens
        with the search engine's own privacy policy is worse than no result list.
    """
    out: List[SearchResult] = []
    seen: set = set()
    engine_domain = ENGINES.get(engine, {}).get("domain", "")
    for index, item in enumerate(raw or [], start=1):
        url = str(item.get("href") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title or url in seen:
            continue
        try:
            host = (urlsplit(url).hostname or "").lower().lstrip("www.")
        except ValueError:
            continue
        if not host or host == engine_domain.lstrip("www."):
            continue
        seen.add(url)
        out.append(
            SearchResult(
                rank=len(out) + 1,
                title=title[:300],
                url=url,
                snippet=str(item.get("snippet") or "").strip()[:600],
                engine=engine,
                parser=parser,
            )
        )
    return out