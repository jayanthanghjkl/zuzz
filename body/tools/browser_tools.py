"""Body/Tools — browser automation.

Fifteen tools wrapping the BrowserActions high-level API:
- browser_open: launch a browser session
- browser_navigate: go to a URL
- browser_click: click an element by selector or text
- browser_fill_form: fill form fields
- browser_extract_text: extract text from a page element
- browser_extract_links: get all links on the current page
- browser_search_web: perform a web search
- browser_download: download a file into quarantine
- browser_screenshot: screenshot the current browser view
- browser_list_tabs: list all open tabs
- browser_switch_tab: switch the active tab
- browser_close_tab: close a specific tab
- browser_close: close a browser session
- browser_scroll: scroll the page
- browser_get_session_status: get session info

Categories follow the browser_actions module:
- browse_internet: navigation, extraction, search, session lifecycle
- web_automation: clicks, typing, form fills, tabs, scrolling
- download_files: downloads (plus write_files for the quarantine write)
- visual_capture: browser screenshots
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from body.legs.browser_actions import BrowserActions
from body.legs.web_search import DEFAULT_ENGINE as DEFAULT_SEARCH_ENGINE
from brain.prefrontal_cortex.action import ActionCategory
from body.tools.tool_spec import ToolContext, ToolRefused, ToolSpec, ToolTarget, required_text


def _get_browser(context: ToolContext) -> BrowserActions:
    """Return the BrowserActions from context, refusing when absent."""
    browser = getattr(context, "browser", None)
    if browser is None:
        raise ToolRefused("browser tools require BrowserActions, which is not attached")
    if not isinstance(browser, BrowserActions):
        raise ToolRefused("context.browser is not a BrowserActions instance")
    return browser


def _get_session_id(params: Dict[str, Any], browser: BrowserActions) -> str:
    """Get the session_id from params, or use the most recent session."""
    session_id = params.get("session_id")
    if session_id:
        return str(session_id)
    sessions = browser.controller.list_sessions()
    if not sessions:
        raise ToolRefused("no open browser session; call browser_open first")
    return sessions[0]["session_id"]


# -- browser_open --------------------------------------------------------------

def _resolve_browser_open(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_open."""
    headless = str(params.get("headless", "false")).lower() == "true"
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target="browser:open" + (" headless" if headless else ""),
        preview={
            "description": f"Open browser{' headless' if headless else ''}",
            "headless": headless,
        },
    )


async def _execute_browser_open(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_open."""
    browser = _get_browser(context)
    headless = str(params.get("headless", "false")).lower() == "true"
    return await browser.open(decision, headless=headless)


# -- browser_navigate ----------------------------------------------------------

def _resolve_browser_navigate(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_navigate."""
    url = required_text(params, "url", "browser_navigate")
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target=url,
        preview={
            "description": f"Navigate to {url}",
            "url": url,
        },
    )


async def _execute_browser_navigate(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_navigate."""
    browser = _get_browser(context)
    url = required_text(params, "url", "browser_navigate")
    session_id = _get_session_id(params, browser)
    return await browser.navigate(decision, session_id=session_id, url=url)


# -- browser_click -------------------------------------------------------------

def _resolve_browser_click(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_click."""
    selector_or_text = required_text(params, "selector_or_text", "browser_click")
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target=f"click:{selector_or_text}",
        preview={
            "description": f"Click '{selector_or_text}'",
            "selector_or_text": selector_or_text,
        },
    )


async def _execute_browser_click(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_click."""
    browser = _get_browser(context)
    selector_or_text = required_text(params, "selector_or_text", "browser_click")
    session_id = _get_session_id(params, browser)
    return await browser.click_element(decision, session_id=session_id, selector_or_text=selector_or_text)


# -- browser_fill_form ---------------------------------------------------------

def _resolve_browser_fill_form(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_fill_form."""
    form_data = params.get("form_data", {})
    if not isinstance(form_data, dict) or not form_data:
        raise ToolRefused("browser_fill_form: form_data must be a non-empty object")
    fields = ", ".join(sorted(form_data.keys()))
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target=f"fill:{fields}",
        preview={
            "description": f"Fill form fields: {fields}",
            "fields": list(form_data.keys()),
        },
    )


async def _execute_browser_fill_form(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_fill_form."""
    browser = _get_browser(context)
    form_data = params.get("form_data", {})
    session_id = _get_session_id(params, browser)
    return await browser.fill_form(decision, session_id=session_id, form_data=form_data)


# -- browser_extract_text ------------------------------------------------------

def _resolve_browser_extract_text(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_extract_text."""
    selector = required_text(params, "selector", "browser_extract_text")
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target=f"extract:text:{selector}",
        preview={
            "description": f"Extract text from '{selector}'",
            "selector": selector,
        },
    )


async def _execute_browser_extract_text(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_extract_text."""
    browser = _get_browser(context)
    selector = required_text(params, "selector", "browser_extract_text")
    session_id = _get_session_id(params, browser)
    return await browser.extract(decision, session_id=session_id, mode="text", selector=selector)


# -- browser_extract_links -----------------------------------------------------

def _resolve_browser_extract_links(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_extract_links."""
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target="extract:links",
        preview={"description": "Extract all links from the page"},
    )


async def _execute_browser_extract_links(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_extract_links."""
    browser = _get_browser(context)
    session_id = _get_session_id(params, browser)
    return await browser.extract(decision, session_id=session_id, mode="links")


# -- browser_search_web --------------------------------------------------------

def _resolve_browser_search_web(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_search_web.

    The target is the **search URL**, taken from the Legs layer's own
    :meth:`~body.legs.browser_actions.BrowserActions.propose_search` rather than
    spelled here. :meth:`BrowserActions.search_web` documents its decision as
    "an allowed ``browse_internet`` decision whose target is the search URL",
    and checks that target against the allowlist. A readable
    ``search:google:Python tutorials`` gets parsed as a URL, and
    ``google:Python tutorials`` is read as host ``google`` with port
    ``Python tutorials`` — so the decision was judged on one thing and the search
    ran against another.
    """
    query = required_text(params, "query", "browser_search_web")
    engine = str(params.get("engine", DEFAULT_SEARCH_ENGINE))
    browser = _get_browser(context)
    action = browser.propose_search(query, engine)
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target=action.target,
        preview={
            "description": f"Search {engine} for '{query}'",
            "query": query,
            "engine": engine,
        },
    )


async def _execute_browser_search_web(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_search_web."""
    browser = _get_browser(context)
    query = required_text(params, "query", "browser_search_web")
    engine = str(params.get("engine", DEFAULT_SEARCH_ENGINE))
    session_id = _get_session_id(params, browser)
    return await browser.search_web(decision, session_id=session_id, query=query, engine=engine)


# -- browser_download ----------------------------------------------------------

def _resolve_browser_download(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_download."""
    url = required_text(params, "url", "browser_download")
    return ToolTarget(
        category=str(ActionCategory.DOWNLOAD_FILES),
        target=url,
        preview={
            "description": f"Download {url} into quarantine",
            "url": url,
        },
    )


async def _execute_browser_download(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_download.

    Requires two decisions: download_files for the URL, and write_files for the
    quarantine path. The write decision target is derived from the download plan.
    """
    browser = _get_browser(context)
    url = required_text(params, "url", "browser_download")
    session_id = _get_session_id(params, browser)

    # Build the download plan to get the target path for the write decision
    plan = browser.plan_download(url, elevated=False)
    write_target = plan["path"]

    # The caller must provide both decisions. The download_files decision is the
    # primary one passed to this tool; the write_files decision must be in context.
    write_decision = getattr(context, "write_decision", None)
    if write_decision is None:
        raise ToolRefused(
            "browser_download requires a write_files decision in context.write_decision "
            f"for the quarantine path: {write_target}"
        )

    return await browser.download_file(
        decision, write_decision, session_id=session_id, url=url
    )


# -- browser_screenshot --------------------------------------------------------

def _resolve_browser_screenshot(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_screenshot."""
    full_page = str(params.get("full_page", "false")).lower() == "true"
    return ToolTarget(
        category=str(ActionCategory.VISUAL_CAPTURE),
        target="browser:screenshot" + (" full" if full_page else ""),
        preview={
            "description": f"Screenshot browser viewport{' (full page)' if full_page else ''}",
            "full_page": full_page,
        },
    )


async def _execute_browser_screenshot(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_screenshot."""
    browser = _get_browser(context)
    full_page = str(params.get("full_page", "false")).lower() == "true"
    session_id = _get_session_id(params, browser)
    image_bytes = await browser.screenshot(decision, session_id=session_id, full_page=full_page)
    return {
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "bytes": len(image_bytes),
        "full_page": full_page,
    }


# -- browser_list_tabs ---------------------------------------------------------

def _resolve_browser_list_tabs(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_list_tabs."""
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target="browser:list_tabs",
        preview={"description": "List all open tabs"},
    )


def _execute_browser_list_tabs(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_list_tabs (not gated; a gauge)."""
    browser = _get_browser(context)
    session_id = _get_session_id(params, browser)
    return browser.list_tabs(session_id)


# -- browser_switch_tab --------------------------------------------------------

def _resolve_browser_switch_tab(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_switch_tab."""
    tab_id = required_text(params, "tab_id", "browser_switch_tab")
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target=f"browser:switch_tab:{tab_id}",
        preview={
            "description": f"Switch to tab {tab_id}",
            "tab_id": tab_id,
        },
    )


async def _execute_browser_switch_tab(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_switch_tab."""
    browser = _get_browser(context)
    tab_id = required_text(params, "tab_id", "browser_switch_tab")
    session_id = _get_session_id(params, browser)
    return await browser.switch_tab(decision, session_id=session_id, tab_id=tab_id)


# -- browser_close_tab ---------------------------------------------------------

def _resolve_browser_close_tab(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_close_tab."""
    tab_id = required_text(params, "tab_id", "browser_close_tab")
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target=f"browser:close_tab:{tab_id}",
        preview={
            "description": f"Close tab {tab_id}",
            "tab_id": tab_id,
        },
    )


async def _execute_browser_close_tab(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_close_tab."""
    browser = _get_browser(context)
    tab_id = required_text(params, "tab_id", "browser_close_tab")
    session_id = _get_session_id(params, browser)
    return await browser.close_tab(decision, session_id=session_id, tab_id=tab_id)


# -- browser_close -------------------------------------------------------------

def _resolve_browser_close(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_close."""
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target="browser:close",
        preview={"description": "Close browser session"},
    )


async def _execute_browser_close(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_close."""
    browser = _get_browser(context)
    session_id = _get_session_id(params, browser)
    return await browser.close(decision, session_id=session_id)


# -- browser_scroll ------------------------------------------------------------

def _resolve_browser_scroll(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_scroll."""
    amount = int(params.get("amount", 0))
    direction = "down" if amount >= 0 else "up"
    return ToolTarget(
        category=str(ActionCategory.WEB_AUTOMATION),
        target=f"browser:scroll:{amount}",
        preview={
            "description": f"Scroll {abs(amount)}px {direction}",
            "amount": amount,
        },
    )


async def _execute_browser_scroll(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_scroll."""
    browser = _get_browser(context)
    amount = int(params.get("amount", 0))
    session_id = _get_session_id(params, browser)
    return await browser.scroll(decision, session_id=session_id, pixels=amount)


# -- browser_get_session_status ------------------------------------------------

def _resolve_browser_get_session_status(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve browser_get_session_status."""
    return ToolTarget(
        category=str(ActionCategory.BROWSE_INTERNET),
        target="browser:status",
        preview={"description": "Get browser session status"},
    )


def _execute_browser_get_session_status(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Execute browser_get_session_status (not gated; a gauge)."""
    browser = _get_browser(context)
    session_id = params.get("session_id")
    if session_id:
        sessions = [s for s in browser.controller.list_sessions() if s["session_id"] == session_id]
        return sessions[0] if sessions else {"error": f"session {session_id} not found"}
    return browser.status()


#: The browser tools.
BROWSER_TOOLS: tuple = (
    ToolSpec(
        name="browser_open",
        summary="Launch a browser session",
        resolve=_resolve_browser_open,
        execute=_execute_browser_open,
        returns="Session ID, headless flag, user agent, and tab count",
    ),
    ToolSpec(
        name="browser_navigate",
        summary="Go to a URL in an open browser session",
        resolve=_resolve_browser_navigate,
        execute=_execute_browser_navigate,
        returns="Navigation record with URL, title, status, and domain info",
    ),
    ToolSpec(
        name="browser_click",
        summary="Click an element on the page by selector or visible text",
        resolve=_resolve_browser_click,
        execute=_execute_browser_click,
        returns="Click record with element info and navigation result",
    ),
    ToolSpec(
        name="browser_fill_form",
        summary="Fill form fields on the current page",
        resolve=_resolve_browser_fill_form,
        execute=_execute_browser_fill_form,
        returns="Fill record with filled fields, missing fields, and submitted flag",
    ),
    ToolSpec(
        name="browser_extract_text",
        summary="Extract text from a page element",
        resolve=_resolve_browser_extract_text,
        execute=_execute_browser_extract_text,
        returns="Text extraction record with text content and metadata",
    ),
    ToolSpec(
        name="browser_extract_links",
        summary="Get all links on the current page",
        resolve=_resolve_browser_extract_links,
        execute=_execute_browser_extract_links,
        returns="List of link records with URL, text, and attributes",
    ),
    ToolSpec(
        name="browser_search_web",
        summary="Perform a web search",
        resolve=_resolve_browser_search_web,
        execute=_execute_browser_search_web,
        returns="Search record with results, engine, and query",
    ),
    ToolSpec(
        name="browser_download",
        summary="Download a file into quarantine through the Fingers classifier",
        resolve=_resolve_browser_download,
        execute=_execute_browser_download,
        returns="Download record with classification, SHA256, and quarantine path",
    ),
    ToolSpec(
        name="browser_screenshot",
        summary="Take a screenshot of the current browser view",
        resolve=_resolve_browser_screenshot,
        execute=_execute_browser_screenshot,
        returns="Base64-encoded PNG with byte count",
    ),
    ToolSpec(
        name="browser_list_tabs",
        summary="List all open tabs in a session",
        resolve=_resolve_browser_list_tabs,
        execute=_execute_browser_list_tabs,
        returns="Tab records with IDs, URLs, titles, and active flag",
    ),
    ToolSpec(
        name="browser_switch_tab",
        summary="Switch the active tab",
        resolve=_resolve_browser_switch_tab,
        execute=_execute_browser_switch_tab,
        returns="Tab record for the newly active tab",
    ),
    ToolSpec(
        name="browser_close_tab",
        summary="Close a specific tab",
        resolve=_resolve_browser_close_tab,
        execute=_execute_browser_close_tab,
        returns="Record of what was closed and remaining tabs",
    ),
    ToolSpec(
        name="browser_close",
        summary="Close a browser session",
        resolve=_resolve_browser_close,
        execute=_execute_browser_close,
        returns="True when a session was closed",
    ),
    ToolSpec(
        name="browser_scroll",
        summary="Scroll the page up or down",
        resolve=_resolve_browser_scroll,
        execute=_execute_browser_scroll,
        returns="Scroll record with position before and after",
    ),
    ToolSpec(
        name="browser_get_session_status",
        summary="Get current session info (URL, tabs, allowlist)",
        resolve=_resolve_browser_get_session_status,
        execute=_execute_browser_get_session_status,
        returns="Session status with live sessions, tabs, downloads, allowlist, and caps",
    ),
)