"""Legs — browser automation behind the gate.

ZUZZ's ability to browse, and the first part of it that lets a third party choose
what ZUZZ reads and does. That is why this package is the most heavily gated part
of the Body: the Hands act on the machine ZUZZ is running on, under the user's own
eye; the Legs act on a machine somewhere else, on instructions written by whoever
controls that page.

**Every public method takes a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` as its
first argument**, with six documented exceptions that are system-level rather than
agent actions:

* :meth:`BrowserController.is_alive` and :meth:`BrowserController.list_sessions`,
  :meth:`TabManager.list_tabs`, :meth:`TabManager.open_count`,
  :meth:`BrowserActions.list_tabs`, :meth:`BrowserActions.status`,
  :meth:`UrlGuard.describe`, :meth:`DataExtractor.caps`,
  :meth:`DownloadHandler.list_quarantine`, :meth:`DownloadHandler.limits`,
  :meth:`SessionManager.describe`, :meth:`SessionManager.persisted_domains`,
  :meth:`WebSearch.engines`, :meth:`FormFiller.detect`,
  :meth:`BrowserActions.list_downloads`, :meth:`BrowserActions.plan_download`,
  :meth:`DownloadHandler.classify`, :meth:`DownloadHandler.plan` and
  :meth:`ElementInteractor.check_confidence` — status, limits and policy. These are
  the panels a user consults *in order to decide* whether to grant a permission. A
  gauge that needed a confirmation would be a gauge nobody looked at, and the
  policy a browser refuses to show is a policy the user cannot argue with.
* :meth:`BrowserActions.propose`, :meth:`BrowserActions.category_for`,
  :meth:`BrowserActions.propose_search`, :meth:`UrlGuard.check` and
  :meth:`BrowserController.navigate` — these either build the decision or run after
  one has already been verified, and :meth:`UrlGuard.check` returns a verdict
  rather than acting on it.
* :meth:`BrowserController.close_all` — the shutdown path. Called from an
  ``asynccontextmanager`` teardown, where requiring a decision to release resources
  would mean a crashed server leaves browsers running. It stops processes ZUZZ
  started and shows the user nothing.
* :meth:`BrowserActions.approve_domain` and :meth:`BrowserActions.forget_domain` —
  these record the *user's* answer to a question ZUZZ asked. A Body method that let
  a caller add to its own allowlist without a decision would be a Body method that
  grants itself permission. The Face layer audits the change instead.
* :meth:`ElementInteractor.describe` — reads an element's shape, not a page's
  contents, and is what a caller uses to work out which selector to click.
* :meth:`SessionManager.is_persisted` — a membership test against the user's own
  approval record.

That list is closed, and ``tests/test_body_legs.py`` pins it with
``test_public_methods_all_require_a_decision``: a new decision-free method is a
test failure, not a quiet widening of the surface that skips the gate.

Three invariants run through every module here:

* **The allowlist is a prompt-removal convenience, not a security boundary.**
  ``blocked`` in ``config/url_allowlist.json`` is the boundary, and it always wins.
* **A page is untrusted input.** Nothing evaluated in a page contains a caller's
  string, extraction is capped, and OCR is not used where the DOM will do.
* **Downloads land in quarantine and stay there** until a user confirms a
  *different* path, and an executable needs an explicit request on top of that.

See :mod:`body.legs.url_guard` for the allowlist, :mod:`body.legs.exceptions` for
how refusals are classified, and :mod:`body.legs.browser_actions` for the order
the checks run in.
"""

from body.legs.browser_actions import CATEGORIES, BrowserActions
from body.legs.browser_controller import BrowserController, BrowserSession
from body.legs.data_extractor import DataExtractor
from body.legs.download_handler import DownloadHandler, DownloadPlan
from body.legs.element_interactor import ElementInteractor
from body.legs.exceptions import (
    BlockedDomainRefused,
    BrowserUnavailable,
    DecisionRefused,
    DecisionReplayRefused,
    DomainConfirmationRequired,
    DownloadRefused,
    DownloadTooLarge,
    DryRunRefused,
    ElementNotFound,
    ElementRefused,
    ExecutableDownloadRefused,
    FormFillRefused,
    InvalidUrlRefused,
    KillSwitchRefused,
    LastTabRefused,
    LegsRefused,
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
from body.legs.form_filler import FormFiller
from body.legs.session_manager import SessionManager
from body.legs.tab_manager import Tab, TabManager
from body.legs.url_guard import UrlGuard, Verdict
from body.legs.web_search import ENGINES, SearchResult, WebSearch

__all__ = [
    "BlockedDomainRefused",
    "BrowserActions",
    "BrowserController",
    "BrowserSession",
    "BrowserUnavailable",
    "CATEGORIES",
    "DataExtractor",
    "DecisionRefused",
    "DecisionReplayRefused",
    "DomainConfirmationRequired",
    "DownloadHandler",
    "DownloadPlan",
    "DownloadRefused",
    "DownloadTooLarge",
    "DryRunRefused",
    "ENGINES",
    "ElementInteractor",
    "ElementNotFound",
    "ElementRefused",
    "ExecutableDownloadRefused",
    "FormFillRefused",
    "FormFiller",
    "InvalidUrlRefused",
    "KillSwitchRefused",
    "LastTabRefused",
    "LegsRefused",
    "NavigationFailed",
    "PrivateNetworkRefused",
    "QuarantineRefused",
    "SchemeRefused",
    "SearchResult",
    "SessionManager",
    "SessionPersistenceRefused",
    "SessionRefused",
    "Tab",
    "TabManager",
    "TabNotFound",
    "TooManyTabsRefused",
    "UrlGuard",
    "UrlRefused",
    "Verdict",
    "WebSearch",
]