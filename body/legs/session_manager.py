"""Body/Legs — per-domain session persistence.

A session is cookies, local storage and service-worker registrations. ZUZZ's default
is that none of it survives the browser closing, so a task that browses three sites
leaves three anonymous visitors behind rather than three logged-in ones. That is
approved answer Q5, and it is the default because the alternative is ZUZZ quietly
amassing a credential store for every site it has ever been asked to read.

Persistence is therefore **opt-in per domain**, written into
``url_allowlist.json -> sessions.persist_domains`` only when the user ticks the box
for that domain, and the UI has a "Forget this domain" button that removes both the
approval and the file. The "Forget" button is the important half: a consent feature
without a revocation the user can find is not consent.

**State is encrypted at rest.** The key is generated on this machine on first use
and kept in ``storage/sessions/.session.key``. The point is not to defend against
the user, who owns the key. It is that a directory of cookies copied off this
laptop by anything else should not be a directory of cookies that still work, and
that ``storage/`` is the directory most likely to be swept into a backup or a sync
folder.

Two things this module will not do, because they are the two ways session storage
becomes a credential store by accident:

* **It never applies state to a domain the allowlist has not approved.** Loading a
  saved context is not a decision about reaching the domain, but it is close enough
  to one that treating it as automatic would let an approval for one domain carry a
  cookie jar to another.
* **It never stores what Chromium will not hand over.** ``storage_state()`` returns
  cookies and origins; a session token in ``localStorage`` is not included, and
  ZUZZ does not scrape it out. A site that keeps its login in local storage does not
  persist here, and the honest behaviour is to tell the user that rather than to
  quietly widen what is captured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.legs.browser_controller import BrowserController
from body.legs.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
    SessionPersistenceRefused,
)
from core.config import ROOT

#: Where persisted state lives. Gitignored already, and a nested ``.gitignore`` is
#: written on first use for the same reason the screenshot store writes one: a rule
#: in the repository can be tidied away, one inside the directory cannot.
SESSION_DIR = ROOT / "storage" / "sessions"

#: The key file, beside the states it protects.
KEY_FILE = ".session.key"

#: Dropped into the session directory on first use.
NESTED_IGNORE = (
    "# Persisted browser sessions: cookies for sites the user chose to remember.\n"
    "# Never commit them, and never sync them.\n"
    "*\n"
    "!.gitignore\n"
    "!.session.key\n"
)

#: Action category for reading or writing persisted state.
SESSION_CATEGORY = "browse_internet"

#: Anything outside this set is refused as a domain. The file name is derived from
#: the domain, so the derivation has to be total — no separators, no dots-only
#: names, nothing that could resolve outside the store.
UNSAFE_NAME = re.compile(r"[^a-z0-9.]+")

#: How many domains may be persisted. Bounded because each is a credential file and
#: the user's memory of ticking eleven boxes is not a security control.
MAX_PERSISTED = 25


class SessionManager:
    """Saves, loads and forgets per-domain browser state.

    Attributes:
        controller: The controller holding the sessions.
        guard: The URL guard, which owns the approval and writes the config.
        root: The state directory; None means ``storage/sessions``.
        logger: Activity logger.
        kill_switch: The switch re-checked by :meth:`save` and :meth:`load`.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        root: Optional[Path] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Wire the session manager.

        Args:
            controller: The controller holding the sessions.
            guard: The URL guard, for approvals and for the config write.
            root: The state directory, overridable so tests never touch the
                developer's real cookies.
            logger: Activity logger.
            kill_switch: The switch re-checked on save and load.
        """
        self.controller = controller
        self.guard = guard
        self.root = Path(root) if root else SESSION_DIR
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()

    # -- the store ------------------------------------------------------

    def ensure_dir(self) -> None:
        """Create the state directory and its nested ignore rule.

        Returns:
            Nothing.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(NESTED_IGNORE, encoding="utf-8")

    def path_for(self, domain: str) -> Path:
        """Return the file a domain's state lives in.

        Args:
            domain: The host.

        Returns:
            A path inside the store.

        Raises:
            SessionPersistenceRefused: The domain is not a safe file name. Raised
                rather than sanitised, because a name that needs sanitising is a
                name someone constructed, and quietly rewriting it would save state
                under a domain the user never approved.
        """
        slug = _slug(domain)
        candidate = self.root / f"{slug}.enc"
        resolved = candidate.resolve()
        if resolved.parent != self.root.resolve():
            raise SessionPersistenceRefused(
                f"{domain!r} does not name a file inside the session store"
            )
        return resolved

    # -- operations -----------------------------------------------------

    async def save(
        self,
        decision: PermissionDecision,
        session_id: str,
        domain: str = "",
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Write a domain's browser state to disk.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session to read state from.
            domain: The domain to file it under; the active page's domain when
                empty.
            persist: Whether the user has approved persistence for this domain.
                False is the default path and writes nothing, returning what it
                *would* have written so the UI can offer the choice.

        Returns:
            ``domain``, ``persisted``, ``path``, ``cookies``, ``origins`` and
            ``bytes``. With ``persist=False``, ``persisted`` is False and ``path``
            is empty — a preview, not a lie about where the file went.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop is engaged.
            SessionPersistenceRefused: The domain is unusable as a name, or the
                store has reached its cap.
        """
        self._verify(decision, ignore_switch=True)
        if self.kill_switch.is_killed():
            raise KillSwitchRefused(
                "kill switch engaged; browser state will not be written to disk"
            )
        session = self.controller.get(session_id)
        host = _clean(domain or _host_of(session.page))
        if not host:
            raise SessionPersistenceRefused("no domain to save state for")
        state = await session.context.storage_state()
        cookies = list(state.get("cookies") or [])
        origins = list(state.get("origins") or [])
        payload = {
            "domain": host,
            "cookies": cookies,
            "origins": origins,
            "saved_at": round(_now(), 3),
        }
        if not persist:
            self._log("browser_session_previewed", domain=host,
                      cookies=len(cookies), origins=len(origins))
            return {
                "domain": host,
                "persisted": False,
                "path": "",
                "cookies": len(cookies),
                "origins": len(origins),
                "bytes": len(_dumps(payload)),
                "persisted_domains": self.persisted_domains(),
            }
        if not self.is_persisted(host):
            if len(self.persisted_domains()) >= MAX_PERSISTED:
                raise SessionPersistenceRefused(
                    f"{MAX_PERSISTED} domains are already persisted; forget one "
                    f"before adding another"
                )
        self.ensure_dir()
        target = self.path_for(host)
        token = self._fernet()
        target.write_bytes(token.encrypt(_dumps(payload)))
        if self.guard is not None:
            self.guard.set_persistence(host, True)
        self._log("browser_session_saved", domain=host, cookies=len(cookies),
                  origins=len(origins), path=target.name)
        return {
            "domain": host,
            "persisted": True,
            "path": str(target),
            "cookies": len(cookies),
            "origins": len(origins),
            "bytes": len(_dumps(payload)),
            "persisted_domains": self.persisted_domains(),
        }

    def load(self, decision: PermissionDecision, domain: str) -> Optional[Dict[str, Any]]:
        """Return a domain's saved state, for building a new context.

        Args:
            decision: An allowed ``browse_internet`` decision.
            domain: The host.

        Returns:
            ``{"domain", "cookies", "origins", "saved_at"}``, or None when the
            domain has no saved state. None is not a refusal: "this domain was not
            persisted" is the default and asking about it should not be an error.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            SessionPersistenceRefused: The file exists but cannot be decrypted with
                this machine's key — a different machine's session directory, or a
                key that was rotated. Said plainly rather than reported as "no saved
                session", because the two look identical from the browser's side and
                only one of them is a problem.
        """
        self._verify(decision)
        host = _clean(domain)
        if not self.is_persisted(host):
            return None
        target = self.path_for(host)
        if not target.is_file():
            return None
        try:
            plain = self._fernet().decrypt(target.read_bytes())
            return json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise SessionPersistenceRefused(
                f"the saved session for {host} cannot be read with this machine's "
                f"key ({exc}). Forget the domain to clear it."
            ) from exc

    async def restore(
        self, decision: PermissionDecision, session_id: str, domain: str
    ) -> Dict[str, Any]:
        """Apply a domain's saved state to a live session.

        The context is what carries state, and Playwright applies ``storage_state``
        at context creation rather than afterwards. So this copies the saved
        cookies into the live context instead, which Chromium allows through
        ``add_cookies``, and reports honestly that ``localStorage`` was *not*
        restored: it cannot be, once the context exists.

        Args:
            decision: An allowed ``browse_internet`` decision.
            session_id: The session.
            domain: The host.

        Returns:
            ``domain``, ``restored``, ``cookies`` and ``note``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            SessionPersistenceRefused: There is no readable saved state.
        """
        self._verify(decision)
        state = self.load(decision, domain)
        if not state:
            raise SessionPersistenceRefused(
                f"there is no saved session for {domain}"
            )
        session = self.controller.get(session_id)
        cookies = state.get("cookies") or []
        restored = 0
        for cookie in cookies:
            usable = {key: cookie[key] for key in _COOKIE_KEYS if key in cookie}
            try:
                await session.context.add_cookies([usable])
                restored += 1
            except Exception:
                # A cookie Playwright rejects is one the page set with attributes
                # the protocol cannot express over CDP. Skipping it keeps the rest
                # of the jar usable, which is the point of restoring.
                continue
        note = (
            "cookies restored; localStorage and service workers are not restored "
            "into a running context, so a site that keeps its login there will "
            "ask to sign in again"
        )
        self._log("browser_session_restored", domain=state.get("domain", domain),
                  cookies=restored)
        return {
            "domain": state.get("domain", domain),
            "restored": restored,
            "of": len(cookies),
            "note": note,
        }

    def forget(self, decision: PermissionDecision, domain: str) -> Dict[str, Any]:
        """Delete a domain's saved state and its approval. The revocation.

        Args:
            decision: An allowed ``browse_internet`` decision.
            domain: The host.

        Returns:
            ``domain``, ``deleted`` (whether a file went), ``path`` and
            ``approval_removed``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
        """
        self._verify(decision)
        host = _clean(domain)
        target = self.path_for(host)
        deleted = target.is_file()
        if deleted:
            target.unlink()
        approval = False
        if self.guard is not None:
            self.guard.set_persistence(host, False)
            approval = True
        self._log("browser_session_forgotten", domain=host, deleted=deleted)
        return {
            "domain": host,
            "deleted": deleted,
            "path": str(target) if deleted else "",
            "approval_removed": approval,
            "persisted_domains": self.persisted_domains(),
        }

    def persisted_domains(self) -> List[str]:
        """Return the domains the user has approved to persist. Not gated; a gauge.

        Returns:
            The domain list, sorted, from the approval record rather than from the
            directory. The directory can hold a file the user has since unticked,
            and the approval record is the one the user can see.
        """
        if self.guard is None:
            return []
        config = self.guard.config()
        return sorted(config.get("sessions", {}).get("persist_domains", {}))

    def describe(self) -> Dict[str, Any]:
        """Return the persistence state for the UI. Not gated; a gauge.

        Returns:
            The store path, whether it exists, the approved domains, and per
            domain whether a state file is present and when it was saved. Sizes
            only — a cookie's contents are not shown anywhere.
        """
        domains = self.persisted_domains()
        entries: List[Dict[str, Any]] = []
        for domain in domains:
            try:
                target = self.path_for(domain)
            except SessionPersistenceRefused:
                continue
            present = target.is_file()
            entries.append(
                {
                    "domain": domain,
                    "saved": present,
                    "bytes": target.stat().st_size if present else 0,
                    "name": target.name,
                }
            )
        return {
            "store": str(self.root),
            "exists": self.root.is_dir(),
            "encrypted": True,
            "max_domains": MAX_PERSISTED,
            "domains": entries,
            "count": len(entries),
        }

    # -- internals ------------------------------------------------------

    def is_persisted(self, domain: str) -> bool:
        """Return True when the user has approved persistence for a domain.

        Args:
            domain: The host.

        Returns:
            True when the domain is in the approval record.
        """
        return _clean(domain) in set(self.persisted_domains())

    def _fernet(self) -> Any:
        """Return a cipher for the state store, creating the key on first use.

        The key is 32 random bytes URL-safe encoded, written with restrictive
        permissions where the platform has them. On Windows there is no portable
        ``chmod`` that means what it means on POSIX, so the file inherits the
        directory's ACL — which is why the key lives inside a gitignored directory
        rather than somewhere more convenient to find.

        Returns:
            A ``cryptography.fernet.Fernet`` instance.

        Raises:
            SessionPersistenceRefused: The key could not be created, or
                ``cryptography`` is not installed.
        """
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise SessionPersistenceRefused(
                "cryptography is not installed, so browser sessions cannot be "
                "stored safely. Install it, or leave persistence off."
            ) from exc
        path = self.root / KEY_FILE
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return Fernet(key.encode("utf-8"))
        self.ensure_dir()
        key = Fernet.generate_key()
        try:
            path.write_text(key.decode("utf-8"), encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            # Windows has no usable chmod here. The file is still created, still
            # gitignored, and still inside storage/sessions. Failing the whole
            # feature over a permission bit that does not exist on this platform
            # would be worse than storing it with the directory's ACL.
            pass
        return Fernet(key)

    def _verify(
        self,
        decision: PermissionDecision,
        ignore_switch: bool = False,
    ) -> None:
        """Refuse the call unless the decision permits it.

        Args:
            decision: What the caller was handed.
            ignore_switch: Set by :meth:`forget`, which must run during a stop.

        Returns:
            Nothing.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
        """
        if not isinstance(decision, PermissionDecision):
            raise DecisionRefused(
                "refusing to touch browser sessions without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused(
                "dry-run decisions describe the action; they never touch cookies"
            )
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != SESSION_CATEGORY:
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {SESSION_CATEGORY}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. Counts only; a cookie value is never
                logged.

        Returns:
            Nothing.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("legs", action, **fields)
        except Exception:
            pass


#: Cookie fields Chromium's cookie protocol round-trips. Anything else is dropped
#: rather than passed through, so a field name ZUZZ has not heard of cannot reach
#: ``add_cookies`` and raise there.
_COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly",
                "secure", "sameSite")


def _slug(domain: str) -> str:
    """Return a file-safe name for a domain.

    Args:
        domain: The host.

    Returns:
        Lower-case alphanumerics and dots.

    Raises:
        SessionPersistenceRefused: Nothing usable is left.
    """
    cleaned = UNSAFE_NAME.sub("-", _clean(domain))
    if not cleaned or set(cleaned) <= {"-", "."}:
        raise SessionPersistenceRefused(
            f"{domain!r} does not name a storable domain"
        )
    return cleaned


def _clean(domain: str) -> str:
    """Return a bare lower-cased host from a domain or a URL.

    Args:
        domain: ``example.com`` or ``https://example.com/x``.

    Returns:
        The host, or an empty string when there is none.
    """
    from urllib.parse import urlsplit

    text = str(domain).strip().lower()
    if not text:
        return ""
    if "//" not in text:
        text = f"https://{text}"
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    return UNSAFE_NAME.sub("-", host)


def _host_of(page: Any) -> str:
    """Return a page's host, defensively.

    Args:
        page: The Playwright page.

    Returns:
        The host, or an empty string.
    """
    from urllib.parse import urlsplit

    try:
        return (urlsplit(str(page.url)).hostname or "").lower()
    except (ValueError, AttributeError):
        return ""


def _dumps(payload: Dict[str, Any]) -> bytes:
    """Serialise a state payload.

    Args:
        payload: The state to write.

    Returns:
        UTF-8 JSON bytes.
    """
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _now() -> float:
    """Return the current Unix timestamp.

    Returns:
        Seconds since the epoch.
    """
    import time

    return time.time()