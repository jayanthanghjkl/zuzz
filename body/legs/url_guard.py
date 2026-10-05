"""Body/Legs — URL allowlist.

Every navigation is checked here before a page is requested, and the check happens
in the Body rather than only in the API because the API is not the only caller:
the chat action parser and the web-search helper both reach the browser directly.

**The allowlist is a prompt-removal convenience, not a security boundary.** It is
the same relationship :mod:`config.apps_whitelist` has to the Hands layer: being
listed means "do not ask", not "is safe". The list that actually decides what can
happen is the ``blocked`` block, which always wins, and the reading here is built
so that adding a domain to ``user_added`` cannot make the browser more dangerous
than it was.

Four answers come out of :meth:`UrlGuard.check`, and the difference between them
is the whole design:

* **blocked** — never navigable, at any trust level. A refusal, not a question.
* **allowed** — the user has already said yes. Navigate.
* **confirm** — reachable, but the user has not seen it. Raise
  :class:`~body.legs.exceptions.DomainConfirmationRequired` so the caller turns it
  into a prompt.
* **ask again** — the domain is listed but the request is an *explicit user
  command*, which the approved invariant 2 says always asks. A URL typed by hand
  and a URL ZUZZ chose for itself are indistinguishable at the socket, so this is
  the only place they can be told apart.

The config is re-read on every lookup rather than memoised, for the same reason
:func:`core.config.get_trust_levels` is not: a narrower allowlist must never be
hidden behind a stale cache, and the UI writes into this file directly.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.legs.exceptions import (
    BlockedDomainRefused,
    DecisionReplayRefused,
    DomainConfirmationRequired,
    DryRunRefused,
    InvalidUrlRefused,
    KillSwitchRefused,
    PrivateNetworkRefused,
    SchemeRefused,
)
from core.config import CONFIG_DIR

#: The config file. Named, not inlined, because the tests and the UI both point
#: at a different one.
ALLOWLIST_FILE = "url_allowlist.json"

#: Hosts with no dot at all — ``intranet``, ``router`` — are refused outright.
#: A bare hostname resolves through whatever search domain the machine happens to
#: have, which makes it a way to say ``localhost`` without spelling it.
BARE_HOSTNAME_REFUSED = True

#: Control characters and whitespace in a URL are always a mistake or an attempt
#: to smuggle a second URL past a prefix match. Neither is worth a guess.
CONTROL_CHARS = re.compile(r"[\x00-\x20\x7f]")

#: An explicit scheme at the start of a URL, per RFC 3986. Tested before the
#: default scheme is added, because ``data:text/html,x`` looks schemeless to a
#: ``"//" in text`` check and would be rewritten into ``https://data:text/...``,
#: turning a scheme refusal into a confusing "no dot in it" one.
SCHEME_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

#: Scheme used when the caller gives a bare host or path.
DEFAULT_SCHEME = "https"

#: Schemes the browser refuses to navigate to regardless of the allowlist.
#: Duplicated here as a floor, because a config file is an editable file and a
#: missing or emptied list must not widen what is reachable.
NEVER_SCHEMES = frozenset(
    {"file", "ftp", "data", "javascript", "blob", "chrome", "view-source", "about"}
)

#: The categories a domain can be filed under in the UI. Free text in the config,
#: but these are the ones the page renders a label for.
CATEGORIES = ("search", "reference", "code", "programming", "web_docs", "python",
              "packages", "ai", "social", "news", "shopping", "other")


@dataclass(frozen=True)
class Verdict:
    """What :meth:`UrlGuard.check` concluded about one URL.

    Attributes:
        url: The normalised URL that was judged.
        domain: Its host, lower-cased.
        allowed: True when the user has already approved this domain.
        blocked: True when the URL is never navigable at any trust level.
        needs_confirmation: True when it is reachable but unapproved.
        reason: One line for the UI, the log and the audit trail.
        source: Where the answer came from — ``preset``, ``user_added``,
            ``session``, ``explicit`` or ``blocked``.
        category: The allowlist category, when the domain is listed.
        scripting: ``allow``, ``deny`` or ``ask`` for this page, from the
            ``javascript`` block.
    """

    url: str
    domain: str
    allowed: bool
    blocked: bool
    needs_confirmation: bool
    reason: str
    source: str = ""
    category: str = ""
    scripting: str = "deny"
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API and the confirmation popup.

        Returns:
            Every attribute, as a plain dict.
        """
        return {
            "url": self.url,
            "domain": self.domain,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "needs_confirmation": self.needs_confirmation,
            "reason": self.reason,
            "source": self.source,
            "category": self.category,
            "scripting": self.scripting,
        }


class UrlGuard:
    """The allowlist, and the verdict it gives about a URL.

    Attributes:
        config: The allowlist mapping, read fresh on every lookup.
        logger: Activity logger; None means nothing is logged.
        kill_switch: The switch re-checked by :meth:`verify`.
        path: The file edits are written back to.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
        path: Optional[Path] = None,
        trust_manager: Optional[Any] = None,
    ) -> None:
        """Wire the guard.

        Args:
            config: An allowlist to use instead of the file, for tests.
            logger: Activity logger.
            kill_switch: The switch :meth:`verify` re-checks; None means the
                process-wide one, which is the same object the gate uses.
            path: The file to write approved domains back to; None means
                ``config/url_allowlist.json``.
            trust_manager: The app's trust manager. Consulted through
                :meth:`session_policy` so the "explicit command only" and
                "persist this domain" rules answer from the level the user can see in
                the dashboard, rather than from a trust level the guard invented for
                itself and could disagree with the gate about.
        """
        self._config = config
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.path = Path(path) if path else CONFIG_DIR / ALLOWLIST_FILE
        self.trust_manager = trust_manager

    # -- config ---------------------------------------------------------

    def config(self) -> Dict[str, Any]:
        """Return the current allowlist.

        Returns:
            The parsed ``url_allowlist.json``, re-read from disk unless an
            injected config was supplied. An unreadable or malformed file yields an
            empty mapping, which refuses everything — a broken allowlist must fail
            closed, or editing it into a typo would quietly grant the whole web.
        """
        if self._config is not None:
            return self._config
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._log("url_allowlist_unreadable", path=str(self.path), error=str(exc))
            return {}

    def describe(self) -> Dict[str, Any]:
        """Return the allowlist for the UI. A gauge, not an action.

        Returns:
            The presets, what the user has added and removed, the persisted
            session domains, the blocked list and the caps — everything the page
            needs to render "what will ZUZZ ask about" without guessing.
        """
        config = self.config()
        return {
            "presets": sorted(config.get("presets", {})),
            "user_added": sorted(config.get("user_added", {})),
            "removed_presets": sorted(config.get("user_removed_presets", [])),
            "persist_domains": sorted(
                config.get("sessions", {}).get("persist_domains", {})
            ),
            "blocked_exact": sorted(config.get("blocked", {}).get("exact", [])),
            "blocked_suffix": sorted(config.get("blocked", {}).get("suffix", [])),
            "blocked_schemes": sorted(
                set(NEVER_SCHEMES)
                | set(config.get("blocked", {}).get("blocked_schemes", []))
            ),
            "tabs": config.get("tabs", {}),
            "downloads": {
                key: value
                for key, value in config.get("downloads", {}).items()
                if key != "_comment"
            },
            "javascript": config.get("javascript", {}),
            "unknown_domain": config.get("policy", {}).get("unknown_domain", "ask"),
            "explicit_command_always_asks": bool(
                config.get("explicit_command_always_asks", {}).get("value", True)
            ),
        }

    # -- the check -----------------------------------------------------

    def check(
        self,
        url: str,
        approved: Iterable[str] = (),
        trust_level: int = 1,
        explicit_user_command: bool = False,
    ) -> Verdict:
        """Decide whether a URL may be navigated to.

        Args:
            url: As the caller has it — with or without a scheme.
            approved: Domains the user approved for this session, which sit
                between the presets and ``user_added``: they last as long as the
                browser does and are gone when it closes.
            trust_level: The current level, for the loopback floor.
            explicit_user_command: True when the user named this URL themselves.
                Per approved invariant 2 that always asks, even for a listed
                domain.

        Returns:
            The verdict. Never raises for a URL that is merely unknown — that is
            :attr:`Verdict.needs_confirmation`, because a question is not a
            refusal.
        """
        config = self.config()
        try:
            normalised, scheme, host = _split(url)
        except InvalidUrlRefused as exc:
            return Verdict(
                url=str(url), domain="", allowed=False, blocked=True,
                needs_confirmation=False, reason=str(exc), source="blocked",
            )
        blocked = self._blocked_reason(config, scheme, host)
        if blocked:
            return Verdict(
                url=normalised, domain=host, allowed=False, blocked=True,
                needs_confirmation=False, reason=blocked, source="blocked",
            )
        listed = self._listed(config, host)
        scripting = self._scripting(config, listed)
        if explicit_user_command and self._explicit_always_asks(config):
            return Verdict(
                url=normalised, domain=host, allowed=False, blocked=False,
                needs_confirmation=True,
                reason=(
                    f"you asked for {host} directly, so ZUZZ confirms it even "
                    f"though the domain is on the list"
                ),
                source="explicit", category=listed[1], scripting=scripting,
            )
        source, category = listed
        if source:
            return Verdict(
                url=normalised, domain=host, allowed=True, blocked=False,
                needs_confirmation=False,
                reason=f"{host} is on the allowlist ({source})", source=source,
                category=category, scripting=scripting,
            )
        if host in set(approved) or _parent_allowed(host, approved):
            return Verdict(
                url=normalised, domain=host, allowed=True, blocked=False,
                needs_confirmation=False,
                reason=f"{host} was approved for this session",
                source="session", scripting=scripting,
            )
        return Verdict(
            url=normalised, domain=host, allowed=False, blocked=False,
            needs_confirmation=True,
            reason=f"{host} is not on the allowlist; confirm it to continue",
            source="", scripting=scripting,
        )

    def verify(
        self,
        decision: PermissionDecision,
        url: str,
        category: str,
        approved: Iterable[str] = (),
        trust_level: int = 1,
        explicit_user_command: bool = False,
    ) -> Verdict:
        """Refuse the call unless the decision permits it and the URL is reachable.

        This is the guard every Legs method runs first, and it runs the two checks
        in that order: the decision, then the URL. A stopped ZUZZ must be reported
        as stopped even when the URL would have been refused anyway, because the
        two mean different things to whoever reads the log.

        Args:
            decision: The decision the gate issued.
            url: The URL about to be navigated to.
            category: The action category this call performs.
            approved: Session-approved domains.
            trust_level: Current trust level.
            explicit_user_command: Whether the user named this URL themselves.

        Returns:
            The verdict, when the URL may be navigated to.

        Raises:
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop engaged after the decision was issued.
            DecisionRefused: There is no decision, or it does not allow this.
            DecisionReplayRefused: The decision is for another category or URL.
            SchemeRefused: The scheme is never navigable.
            BlockedDomainRefused: The domain is on the never-navigable list.
            PrivateNetworkRefused: The host is loopback, link-local or private.
            InvalidUrlRefused: The string is not a URL.
            DomainConfirmationRequired: The domain is unknown and unapproved.
        """
        if not isinstance(decision, PermissionDecision):
            from body.legs.exceptions import DecisionRefused

            raise DecisionRefused(
                "refusing to browse without a PermissionDecision from the gate"
            )
        if decision.dry_run:
            raise DryRunRefused("dry-run decisions describe the action; they never browse")
        if self.kill_switch.is_killed():
            raise KillSwitchRefused("kill switch engaged since this decision was issued")
        if decision.blocked or not decision.allowed:
            from body.legs.exceptions import DecisionRefused

            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        granted = str(decision.action.category)
        if granted != str(category):
            raise DecisionReplayRefused(
                f"decision was for {granted}, not {category}"
            )
        verdict = self.check(url, approved, trust_level, explicit_user_command)
        # A hard block is checked before the target match, not after. It does not
        # matter what the gate decided about: localhost and the private ranges are
        # refused whatever the decision was for, so reporting a replay here would
        # tell the caller to go and get a different permission when no permission
        # on this machine would help.
        if verdict.blocked:
            if "scheme" in verdict.reason:
                raise SchemeRefused(verdict.reason)
            if "private" in verdict.reason or "loopback" in verdict.reason:
                raise PrivateNetworkRefused(verdict.reason)
            raise BlockedDomainRefused(verdict.reason)
        # A decision made before the scheme was known still covers the page: the
        # gate decides on whatever the caller typed, and "github.com" and
        # "https://github.com/x" are one request as far as the user is concerned.
        # Anything else is a replay.
        if not _same_target(decision.action.target, verdict.url) and not _domain_only(
            decision.action.target, verdict.domain
        ):
            raise DecisionReplayRefused(
                f"decision was for {decision.action.target}, not {verdict.url}"
            )
        if verdict.needs_confirmation:
            raise DomainConfirmationRequired(
                verdict.domain,
                verdict.url,
                verdict.reason,
            )
        return verdict

    # -- allowlist edits ------------------------------------------------

    def approve(
        self,
        domain: str,
        category: str = "other",
        persist_session: bool = False,
        remember: bool = True,
    ) -> Dict[str, Any]:
        """Add a domain the user just approved.

        Args:
            domain: The host to allow.
            category: A label from :data:`CATEGORIES` for the UI.
            persist_session: Whether to keep cookies for this domain between
                sessions. Off by default and asked for per domain, per approved
                answer Q5.
            remember: False records the approval for this session only, which is
                the honest reading of a user who said yes to one page.

        Returns:
            What changed, for the audit record.

        Raises:
            BlockedDomainRefused: The domain is on the never-navigable list, so
                there is nothing to approve.
        """
        host = _bare_domain(domain)
        verdict = self.check(f"https://{host}")
        if verdict.blocked:
            raise BlockedDomainRefused(verdict.reason)
        config = self.config()
        entry = {"category": category if category in CATEGORIES else "other",
                 "added": "user"}
        if remember:
            # A domain that ships in ``presets`` and was explicitly removed comes
            # back as a user entry rather than only having the removal lifted: that
            # way a user who approves it a second time has still chosen it.
            removed = config.setdefault("user_removed_presets", [])
            if host in removed:
                removed.remove(host)
            config.setdefault("user_added", {})[host] = entry
        if persist_session:
            config.setdefault("sessions", {}).setdefault("persist_domains", {})[host] = {
                "category": entry["category"]
            }
        if remember or persist_session:
            self._write(config)
        self._log("url_allowlist_changed", domain=host, action="approve",
                  remembered=remember, persisted=persist_session)
        return {"domain": host, "remembered": remember,
                "persisted": persist_session, "category": entry["category"]}

    def forget(self, domain: str) -> Dict[str, Any]:
        """Undo an approval, for the "Forget this domain" button.

        Removes the domain from ``user_added``, from the persisted-session list and
        from the preset list by recording the removal, so the next navigation asks
        again even though the domain ships in ``presets``. The record of the
        removal is what makes this a forget rather than a hide: it survives a
        config rewrite.

        Args:
            domain: The host to forget.

        Returns:
            What changed, for the audit record.
        """
        host = _bare_domain(domain)
        config = self.config()
        changed: List[str] = []
        if config.get("user_added", {}).pop(host, None) is not None:
            changed.append("allowlist")
        if config.get("sessions", {}).get("persist_domains", {}).pop(host, None) is not None:
            changed.append("session")
        if host in config.get("presets", {}) and host not in config.get(
            "user_removed_presets", []
        ):
            config.setdefault("user_removed_presets", []).append(host)
            changed.append("preset")
        if changed:
            self._write(config)
        self._log("url_allowlist_changed", domain=host, action="forget",
                  changed=",".join(changed) or "nothing")
        return {"domain": host, "changed": changed}

    def set_persistence(self, domain: str, enabled: bool) -> Dict[str, Any]:
        """Turn per-domain cookie persistence on or off.

        Args:
            domain: The host.
            enabled: Whether cookies should survive the session ending.

        Returns:
            What changed, for the audit record.

        Raises:
            BlockedDomainRefused: The domain is never navigable, so there is
                nothing to persist.
        """
        host = _bare_domain(domain)
        verdict = self.check(f"https://{host}")
        if verdict.blocked:
            raise BlockedDomainRefused(verdict.reason)
        config = self.config()
        sessions = config.setdefault("sessions", {}).setdefault("persist_domains", {})
        if enabled:
            sessions[host] = {"category": self.check(f"https://{host}").category or "other"}
        else:
            sessions.pop(host, None)
        self._write(config)
        self._log("url_allowlist_changed", domain=host,
                  action="persist" if enabled else "unpersist")
        return {"domain": host, "persisted": enabled}

    # -- internals ------------------------------------------------------

    def _blocked_reason(
        self, config: Dict[str, Any], scheme: str, host: str
    ) -> str:
        """Return why this URL can never be navigated to, or an empty string.

        Args:
            config: The allowlist.
            scheme: The parsed scheme, already lower-cased.
            host: The parsed host, already lower-cased.

        Returns:
            A one-line reason, or ``""`` when the URL is not on a blocked list.
        """
        blocked = config.get("blocked", {})
        if scheme in NEVER_SCHEMES or scheme in blocked.get("blocked_schemes", []):
            return f"the {scheme} scheme is never navigable from ZUZZ's browser"
        allowed = config.get("policy", {}).get("allowed_schemes", ["https", "http"])
        if allowed and scheme not in allowed:
            return f"the {scheme} scheme is not on the allowed list"
        if not host:
            return "the URL has no host"
        if CONTROL_CHARS.search(host):
            return "the host contains characters no URL may contain"
        for exact in blocked.get("exact", []):
            if host == str(exact).lower():
                return f"{host} is never navigable from ZUZZ's browser"
        for suffix in blocked.get("suffix", []):
            tail = str(suffix).lower()
            if host == tail.lstrip(".") or host.endswith(
        tail if tail.startswith(".") else f".{tail}"
    ):
                return f"{host} ends in {tail}, which is never navigable"
        if _is_private(host, blocked.get("private_ranges", [])):
            return (
                f"{host} is a private, loopback or link-local address; a page "
                f"ZUZZ opens must not be able to reach the network behind it"
            )
        if BARE_HOSTNAME_REFUSED and "." not in host and ":" not in host:
            return (
                f"{host} has no dot in it and would resolve through the machine's "
                f"search domain; spell the full name"
            )
        return ""

    def _listed(self, config: Dict[str, Any], host: str) -> Tuple[str, str]:
        """Return ``(source, category)`` for a host that is on a list.

        Args:
            config: The allowlist.
            host: The lower-cased host.

        Returns:
            ``("preset"|"user_added", category)``, or ``("", "")`` when the host is
            not listed.
        """
        removed = set(config.get("user_removed_presets", []))
        for source, key in (("preset", "presets"), ("user_added", "user_added")):
            table = config.get(key, {})
            for domain, meta in table.items():
                if source == "preset" and domain in removed:
                    continue
                if _domain_matches(host, str(domain),
                                   config.get("policy", {}).get("match_subdomains", True)):
                    category = meta.get("category", "") if isinstance(meta, dict) else ""
                    return source, category
        return "", ""

    def _scripting(self, config: Dict[str, Any], listed: Tuple[str, str]) -> str:
        """Return ``allow``, ``deny`` or ``ask`` for a page.

        Args:
            config: The allowlist.
            listed: The ``(source, category)`` :meth:`_listed` returned.

        Returns:
            The scripting verdict for a page on this domain.
        """
        policy = config.get("javascript", {})
        if listed[0]:
            return str(policy.get("on_trusted", "allow"))
        return str(policy.get("on_untrusted", "deny"))

    def _explicit_always_asks(self, config: Dict[str, Any]) -> bool:
        """Return whether an explicit user command always asks.

        Args:
            config: The allowlist.

        Returns:
            The flag from ``explicit_command_always_asks``.
        """
        return bool(config.get("explicit_command_always_asks", {}).get("value", True))

    def _write(self, config: Dict[str, Any]) -> None:
        """Write the allowlist back to disk.

        The JSON is rewritten whole rather than patched, because patching a file a
        user also edits by hand is how two writers lose each other's changes. The
        read-modify-write here is under the same rule as every other Body write: the
        caller has already been given a decision.

        Args:
            config: The mapping to persist.

        Returns:
            Nothing.

        Raises:
            OSError: The file could not be written. Deliberately not swallowed —
                an allowlist change the user was told succeeded but which did not
                reach the disk is worse than an error.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        self._config = None

    def _log(self, event: str, **fields: Any) -> None:
        """Record an allowlist event in the activity log, when wired.

        The parameter is ``event`` rather than ``action`` because the allowlist's
        own callers have a field genuinely called ``action`` — approve, forget,
        persist — and a positional name that collides with it makes every one of
        them a TypeError at runtime rather than at review.

        Args:
            event: Short event name.
            **fields: Extra structured detail.

        Returns:
            Nothing. A logger that raises must not turn an allowlist edit into a
            failed call, so its errors are swallowed.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("legs", event, **fields)
        except Exception:
            pass


# ---------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------


def _split(url: str) -> Tuple[str, str, str]:
    """Return ``(normalised_url, scheme, host)`` for a URL ZUZZ can act on.

    A bare host or path gets :data:`DEFAULT_SCHEME`, so ``example.com/x`` works
    from the chat bar the way a browser's own bar does. Credentials embedded in
    the URL are refused rather than stripped: a URL with ``user:pass@`` in it is
    either a phishing shape or a bug, and silently dropping the password would
    turn either into a request the user never saw.

    Args:
        url: The URL as given.

    Returns:
        The normalised URL, its lower-cased scheme and its lower-cased host. The
        host is empty for a scheme that carries none — ``data:`` and ``blob:`` —
        which is not an error here: the scheme check in :meth:`_blocked_reason`
        runs before the host is looked at, and refusing ``data:`` because it has
        no host would report the wrong reason for the right refusal.

    Raises:
        InvalidUrlRefused: The string is empty, carries credentials, or contains
            control characters.
    """
    text = str(url).strip()
    if not text:
        raise InvalidUrlRefused("no URL was given")
    if CONTROL_CHARS.search(text.replace(" ", "")):
        raise InvalidUrlRefused(
            "the URL contains whitespace or control characters; quote the value "
            "and try again"
        )
    if "@" in text.split("//", 1)[-1].split("/", 1)[0]:
        raise InvalidUrlRefused(
            "the URL carries credentials in it; ZUZZ will not send a password "
            "typed into a URL bar"
        )
    if "//" not in text and not SCHEME_PREFIX.match(text):
        text = f"{DEFAULT_SCHEME}://{text.lstrip('/')}"
    parts = urlsplit(text)
    scheme = (parts.scheme or DEFAULT_SCHEME).lower()
    try:
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise InvalidUrlRefused(f"{url!r} has an unreadable host: {exc}") from exc
    path = parts.path or "/"
    netloc = parts.netloc or parts.path or ""
    return urlunsplit((scheme, netloc, path, parts.query, "")), scheme, host


def _bare_domain(domain: str) -> str:
    """Return a host from either a bare domain or a full URL.

    Args:
        domain: ``example.com``, ``https://example.com/x`` or ``EXAMPLE.com``.

    Returns:
        The lower-cased host.

    Raises:
        InvalidUrlRefused: There is no host to extract.
    """
    text = str(domain).strip()
    if "//" not in text and not SCHEME_PREFIX.match(text):
        text = f"https://{text}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if not host:
        raise InvalidUrlRefused(f"{domain!r} has no host")
    return host


def _domain_matches(host: str, listed: str, subdomains: bool) -> bool:
    """Return True when a host is covered by an allowlist entry.

    Args:
        host: The host being navigated to.
        listed: The entry in the allowlist.
        subdomains: Whether ``docs.python.org`` is covered by ``python.org``.

    Returns:
        True when the entry covers this host.
    """
    if host == listed:
        return True
    return bool(subdomains) and host.endswith(f".{listed}")


def _parent_allowed(host: str, approved: Iterable[str]) -> bool:
    """Return True when a session-approved parent domain covers this host.

    Args:
        host: The host being navigated to.
        approved: Session-approved domains.

    Returns:
        True when any approval covers this host as a subdomain.
    """
    return any(_domain_matches(host, _bare_domain(item), True) for item in approved)


def _is_private(host: str, ranges: Iterable[str]) -> bool:
    """Return True when a host is an address only a local machine can reach.

    Literal addresses are checked against the ranges in the config as well as
    against :mod:`ipaddress`, because the config is the thing the user edits and a
    private range they added there must be honoured even if this module has never
    heard of it. A name that is not a literal address is not private here: a
    hostname resolving to a private address is a DNS rebinding problem, and the
    answer to that is not to block the name but to resolve the address the browser
    actually connected to — which :mod:`body.legs.browser_controller` records in
    the navigation result for exactly this reason.

    Args:
        host: The host.
        ranges: CIDR ranges from the config.

    Returns:
        True when the host is loopback, link-local, private or unspecified.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        ):
            return True
    for cidr in ranges:
        try:
            if address is not None and address in ipaddress.ip_network(str(cidr), strict=False):
                return True
        except ValueError:
            continue
    return False


def _same_target(granted: str, acted_on: str) -> bool:
    """Return True when a decision's target covers the URL about to be opened.

    Compared ignoring the fragment, the default port and a trailing slash, because
    the gate decides on whatever the caller typed while the guard acts on the
    normalised form, and a decision for ``example.com/x`` should not be refused
    for ``example.com/x/``.

    Args:
        granted: The decision's target.
        acted_on: The normalised URL.

    Returns:
        True when they name the same resource.
    """
    return _canonical(granted) == _canonical(acted_on)


def _domain_only(granted: str, domain: str) -> bool:
    """Return True when the decision's target is just this host.

    Args:
        granted: The decision's target.
        domain: The host being navigated to.

    Returns:
        True when the target names the whole domain.
    """
    try:
        return _bare_domain(granted) == domain
    except InvalidUrlRefused:
        return False


def _canonical(url: str) -> str:
    """Return a URL reduced to the parts that identify the resource.

    Args:
        url: Any URL.

    Returns:
        ``scheme://host:port/path?query``, lower-cased, with the default port and
        a trailing slash removed.
    """
    try:
        parts = urlsplit(url if "//" in url else f"https://{url}")
    except ValueError:
        return str(url).lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not (
        (parts.scheme == "https" and port == 443)
        or (parts.scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    path = (parts.path or "").rstrip("/")
    return f"{parts.scheme.lower()}://{host}{path}?{parts.query}".lower()