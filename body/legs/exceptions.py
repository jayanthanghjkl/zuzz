"""Body/Legs — browser refusals.

One hierarchy, so the API maps refusals onto HTTP statuses by asking a single
question rather than by enumerating every class in three modules. The rule the
tree encodes is the one that actually matters for a browser:

* **The gate's verdict and the guard's verdict are different refusals.** A
  :class:`DecisionRefused` means the Prefrontal Cortex said no, and a
  :class:`UrlRefused` means the Body refused on its own reading of the URL
  *regardless* of what the gate said. A caller that cannot tell them apart will
  eventually write "retry after confirming", which will never help with the
  second kind.
* **A hard refusal has no confirmation path.** :class:`UrlRefused` subclasses and
  :class:`TooManyTabsRefused` are not questions, so nothing answers them.
  :class:`DomainConfirmationRequired` is the only exception, and it is named for
  what it is: a request to be asked, carrying the domain that was unknown.

The ordering of the subclasses is the API's status mapping: 403 for the gate and
the guard's own refusals, 400 for input the caller got wrong, 409 for state that
changed underfoot (a stop, a vanished session), 413 for size.
"""

from __future__ import annotations


class LegsRefused(Exception):
    """Base of every refusal from :mod:`body.legs`."""


class DecisionRefused(LegsRefused):
    """No decision, or one that does not permit this call.

    Raised for a missing decision, a decision that does not allow execution, and
    a decision issued for a different category or a different target. The last two
    are the replay case: a confirmation granted for "go to github.com" must not be
    spendable on "go to a paste site", or a single approved dialog becomes a
    general-purpose permission.
    """


class DecisionReplayRefused(DecisionRefused):
    """The decision is genuine but for another operation or another target."""


class DryRunRefused(LegsRefused):
    """The decision is a dry run, and a dry run does not execute."""


class KillSwitchRefused(LegsRefused):
    """The emergency stop engaged after the decision was issued.

    Checked before the decision's own verdict, so a refusal that happens during a
    stop is reported as a stop rather than as an unconfirmed permission.
    """


class UrlRefused(LegsRefused):
    """The URL itself is not navigable. Asking again will not change it."""


class InvalidUrlRefused(UrlRefused):
    """The string is not a URL ZUZZ can act on."""


class SchemeRefused(UrlRefused):
    """The scheme is not one the browser is allowed to use.

    ``file``, ``data`` and ``javascript`` are here rather than being left to
    Chromium's own handling because a refusal the Body makes is one the audit log
    records. A scheme Chromium quietly declines leaves no trace.
    """


class BlockedDomainRefused(UrlRefused):
    """The domain is on the never-navigable list.

    Not overridable by adding it to the allowlist and not overridable by trust
    level, which is what makes it a different class from
    :class:`DomainConfirmationRequired`.
    """


class PrivateNetworkRefused(UrlRefused):
    """The host is a loopback, link-local or private-range address.

    Separate from :class:`BlockedDomainRefused` because the two fail for
    different reasons and an operator needs to tell them apart: one is policy, the
    other is the shape an exfiltration attempt takes.
    """


class DomainConfirmationRequired(LegsRefused):
    """The domain is unknown and the user has not approved it.

    The only refusal in this module that is a question. It carries the domain and
    the normalised URL so the confirmation the UI shows is about the thing actually
    about to happen, and :attr:`session_worthy` so the caller's answer can also
    decide whether the domain joins the session allowlist.
    """

    def __init__(self, domain: str, url: str = "", reason: str = "") -> None:
        """Build the request.

        Args:
            domain: The host that is not on any list.
            url: The normalised URL that was refused, for display.
            reason: One line explaining why it is being asked about.
        """
        super().__init__(
            reason or f"{domain} is not on the allowlist; confirm it to continue"
        )
        self.domain = domain
        self.url = url
        self.reason = reason or f"{domain} is not on the allowlist; confirm it to continue"


class TooManyTabsRefused(LegsRefused):
    """The session is already at the hard tab cap.

    Carries :attr:`cap`, :attr:`open` and :attr:`override_possible` so the refusal
    message can tell the user what the override would actually cost rather than
    only that a limit exists.
    """

    def __init__(self, cap: int, open_tabs: int, override_possible: bool = True) -> None:
        """Build the refusal.

        Args:
            cap: The hard limit the session has reached.
            open_tabs: How many tabs it has.
            override_possible: Whether the config permits a confirmed override.
        """
        hint = (
            "; a confirmed override can raise the cap for this session"
            if override_possible
            else "; this session cannot raise the cap"
        )
        super().__init__(
            f"the session has {open_tabs} of {cap} tabs open{hint}"
        )
        self.cap = cap
        self.open = open_tabs
        self.override_possible = override_possible


class SessionRefused(LegsRefused):
    """The session id is unknown, or the session has already been closed."""


class BrowserUnavailable(LegsRefused):
    """Playwright is not installed, or the browser refused to launch.

    Distinct from :class:`SessionRefused` because this is a setup problem the user
    fixes once (``playwright install chromium``) rather than a per-call one, so it
    earns its own class and its own status.
    """


class NavigationFailed(LegsRefused):
    """The page could not be loaded.

    Carries the URL and the browser's own message. A failed navigation is a fact
    about the network, so it is never dressed up as a refusal by the caller.
    """


class ElementNotFound(LegsRefused):
    """No element matched the selector or the text."""

    def __init__(self, wanted: str, matched: int = 0) -> None:
        """Build the refusal.

        Args:
            wanted: What was looked for.
            matched: How many elements matched, so the message can distinguish
                "no such thing" from "five things, which one did you mean".
        """
        detail = (
            f" (matched {matched} elements — name one more precisely)"
            if matched > 1
            else ""
        )
        super().__init__(f"no element matched {wanted!r}{detail}")
        self.wanted = wanted
        self.matched = matched


class TabNotFound(LegsRefused):
    """The session has no tab with that id."""


class LastTabRefused(LegsRefused):
    """The request was to close the session's only tab.

    Distinct from :class:`TabNotFound` on purpose: the tab was found and is open,
    and the refusal is about what closing it would leave behind. A browser with no
    tabs cannot show the user what it is doing, which is the property the visible
    browser exists for, so the last tab goes with the browser and not before it.
    """


class ElementRefused(LegsRefused):
    """The element exists but ZUZZ will not act on it.

    Covers the shapes a selector can reach that clicking or typing into is not a
    decision anyone should be able to make by naming a CSS selector: a file input,
    a hidden field, an element inside a disabled form.
    """


class FormFillRefused(LegsRefused):
    """A form field could not be filled.

    Carries the field key that failed, because "fill_form" failing without saying
    which field leaves the user guessing which of eight inputs was wrong.
    """

    def __init__(self, field: str, reason: str) -> None:
        """Build the refusal.

        Args:
            field: The form data key that could not be filled.
            reason: Why.
        """
        super().__init__(f"cannot fill {field!r}: {reason}")
        self.field = field
        self.reason = reason


class DownloadRefused(LegsRefused):
    """The download was refused before or during the fetch."""


class DownloadTooLarge(LegsRefused):
    """The file is over the configured download cap."""


class ExecutableDownloadRefused(DownloadRefused):
    """The file is executable or script-shaped.

    The one refusal in this module that names a file extension rather than a
    rule, because the rule *is* the extension list: `.exe`, `.msi`, `.ps1` and
    their relatives. It stays refused even at full autonomy unless the user passes
    ``elevated=True`` with an explicitly confirmed decision, which is the only path
    in the browser that requires more than a permission.
    """


class QuarantineRefused(DownloadRefused):
    """The download could not be placed in quarantine.

    Worth its own class because the bytes have already been fetched at this point:
    the failure is about where they would land, not about whether to fetch them,
    and a caller that retries the fetch is doing the wrong thing.
    """


class SessionPersistenceRefused(LegsRefused):
    """A per-domain session could not be saved, loaded or forgotten.

    Never raised for "this domain was not persisted" — that is not a failure, it is
    the default. It is raised when persistence was asked for and could not be
    honoured, because silently dropping cookies would make a logged-in flow look
    like a broken site.
    """