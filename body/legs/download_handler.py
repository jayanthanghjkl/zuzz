"""Body/Legs — downloads.

Every file the browser fetches lands in ``storage/downloads`` first, and nothing
leaves there without a second decision about a second path. Approved answer Q3.

The name "quarantine" is the design, not decoration. A download folder implies
somewhere a file came from and now lives, and the user chose to point it at
Downloads. This is a holding pen: bytes arrive from a third party, unreviewed, and
they stay here until a human does something about them. The Windows folder named
Downloads is never a destination, so a page cannot end up putting something in the
user's desktop view by default.

Four checks stand between a URL and a byte on disk, in this order:

1. **The allowlist.** A download is a request, and it is checked like one. The
   extension list does not save a fetch to ``http://localhost:8080/x``.
2. **The extension.** ``.exe``, ``.msi``, ``.ps1`` and their relatives are refused
   outright — not asked about, refused — unless the user explicitly asked for an
   executable and the gate confirmed it. That is the only capability in the browser
   that needs more than a permission.
3. **The size**, from the ``Content-Length`` header before a byte is read and again
   after, because a header is a claim by the server and a claim is not a fact.
4. **The write**, through :class:`~body.fingers.file_manager.FileManager`, which
   applies its own classification and re-checks the kill switch. The fetch needs a
   ``download_files`` decision and the write needs a separate ``write_files``
   decision for the exact path — two decisions for two acts, because "fetch this"
   and "put it at C:\\..." are not the same request.

The bytes are written by Fingers and nothing in this package writes them itself, so
there is no path by which a download reaches the disk ungated.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.fingers.file_manager import FileManager
from body.legs.browser_controller import BrowserController
from body.legs.exceptions import (
    BlockedDomainRefused,
    DecisionRefused,
    DecisionReplayRefused,
    DownloadTooLarge,
    DryRunRefused,
    ExecutableDownloadRefused,
    InvalidUrlRefused,
    KillSwitchRefused,
    QuarantineRefused,
)
from core.config import ROOT

#: Where downloads land. Relative in the config, resolved here.
QUARANTINE_DIR = ROOT / "storage" / "downloads"

#: Action category for the fetch.
DOWNLOAD_CATEGORY = "download_files"

#: Action category for the write. A separate decision because a separate act.
WRITE_CATEGORY = "write_files"

#: Fallback extensions, used when the config has no list. Duplicated so that
#: deleting the config entry cannot widen this into "anything goes".
ELEVATED_EXTENSIONS = (
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js",
    ".jar", ".hta", ".lnk", ".reg", ".dll", ".msp", ".cpl",
)

#: Fallback size cap in megabytes.
DEFAULT_MAX_MB = 100

#: How much of a response is read before the cap is enforced while streaming. The
#: cap is checked against ``Content-Length`` first, so this only bounds a server
#: that lies about the length.
READ_CHUNK = 65_536

#: Characters that never appear in a filename this module writes.
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")

#: A name that is only dots, or that is a Windows device name, would resolve to
#: something other than itself.
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)},
     *{f"LPT{i}" for i in range(1, 10)}}
)

#: Dropped into the quarantine directory on first use, for the same reason the
#: screenshot and session stores write one.
NESTED_IGNORE = (
    "# Downloaded files, unreviewed, in a holding pen. Never commit them.\n"
    "*\n"
    "!.gitignore\n"
)


@dataclass
class DownloadPlan:
    """What a download would do, computed before any byte is fetched.

    Attributes:
        url: The URL as given.
        destination: The absolute path the file would be written to, always inside
            the quarantine directory.
        filename: The sanitised file name.
        content_type: What the server said it was, when it said anything.
        size_bytes: From ``Content-Length``, or None when the server did not say.
        max_bytes: The cap.
        executable: True when the extension is on the elevated list.
        approved: The allowlist verdict for the URL.
        elevated_requested: Whether the caller said they wanted an executable.
    """

    url: str
    destination: str
    filename: str
    content_type: str = ""
    size_bytes: Optional[int] = None
    max_bytes: int = DEFAULT_MAX_MB * 1_048_576
    executable: bool = False
    approved: Any = None
    elevated_requested: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)

    def preview(self) -> Dict[str, Any]:
        """Return what the confirmation popup shows.

        Returns:
            The URL, the file name, the destination, the declared size and the
            executable verdict. Never the contents.
        """
        return {
            "operation": "download",
            "url": self.url,
            "filename": self.filename,
            "destination": self.destination,
            "content_type": self.content_type,
            "declared_bytes": self.size_bytes,
            "max_bytes": self.max_bytes,
            "executable": self.executable,
            "elevated_requested": self.elevated_requested,
            "quarantined": True,
            "reversible": True,
            "writes_disk": True,
            "leaves_machine": False,
        }


class DownloadHandler:
    """Fetches a URL into quarantine and moves confirmed files out of it.

    Attributes:
        controller: The controller holding the sessions.
        guard: The URL guard, consulted before every fetch.
        file_manager: The Fingers file manager every write goes through.
        root: The quarantine directory; None means ``storage/downloads``.
        logger: Activity logger.
        kill_switch: The switch re-checked on fetch and release.
    """

    def __init__(
        self,
        controller: BrowserController,
        guard: Optional[Any] = None,
        file_manager: Optional[FileManager] = None,
        root: Optional[Path] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Wire the download handler.

        Args:
            controller: The controller holding the sessions.
            guard: The URL guard; without it nothing is fetched, because a fetch
                with no allowlist check is exactly the shape this package exists
                to prevent.
            file_manager: The Fingers manager writes go through; built when None.
            root: The quarantine directory, overridable so tests never write into
                the developer's real storage.
            logger: Activity logger.
            kill_switch: The switch re-checked on fetch and release.
        """
        self.controller = controller
        self.guard = guard
        self.file_manager = file_manager or FileManager(logger=logger)
        self.root = Path(root) if root else QUARANTINE_DIR
        self.logger = logger
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()

    # -- planning -------------------------------------------------------

    def limits(self) -> Dict[str, Any]:
        """Return the download policy as configured.

        Returns:
            ``quarantine_dir``, ``max_bytes``, ``elevated_extensions`` and
            ``classify_before_release``. A missing list falls back to
            :data:`ELEVATED_EXTENSIONS`, so a truncated config is stricter rather
            than permissive.
        """
        settings = _settings(self.guard)
        max_mb = settings.get("max_file_size_mb", DEFAULT_MAX_MB)
        try:
            max_bytes = int(max_mb) * 1_048_576
        except (TypeError, ValueError):
            max_bytes = DEFAULT_MAX_MB * 1_048_576
        extensions = settings.get("require_elevated_permission_for") or ELEVATED_EXTENSIONS
        return {
            "quarantine_dir": str(self.root),
            "max_bytes": max_bytes,
            "max_mb": max_bytes // 1_048_576,
            "elevated_extensions": sorted(str(item).lower() for item in extensions),
            "classify_before_release": bool(
                settings.get("classify_before_release", True)
            ),
            "require_confirmation_to_move": bool(
                settings.get("require_confirmation_to_move", True)
            ),
        }

    def plan(self, url: str, filename: str = "", elevated: bool = False) -> DownloadPlan:
        """Work out where a download would go, without fetching anything.

        Not gated. It reads the URL and the local config and performs no I/O, which
        makes it the preview the confirmation popup is built from — and it is
        deliberately the same code path the fetch takes, so the path shown to the
        user is the path the bytes land on.

        Args:
            url: What to download.
            filename: Override the name taken from the URL.
            elevated: The caller said they want an executable.

        Returns:
            The plan. ``approved`` carries the allowlist verdict, which the caller
            turns into a 202 or a 403 before fetching.

        Raises:
            InvalidUrlRefused: The URL is not one the guard will even look at.
        """
        limits = self.limits()
        verdict = (
            self.guard.check(url) if self.guard is not None
            else _unverified(url)
        )
        name = _safe_name(filename or _name_from_url(url) or "download")
        path = (self.root / name).resolve()
        # The name is sanitised above, and the parent is re-checked here. Two
        # checks of the same property is deliberate: this is the line where an
        # attacker-chosen name becomes a path, and the check that matters is the
        # one at the point of use.
        if path.parent != self.root.resolve():
            raise InvalidUrlRefused(
                f"{name!r} does not name a file inside the quarantine directory"
            )
        return DownloadPlan(
            url=url,
            destination=str(path),
            filename=name,
            max_bytes=limits["max_bytes"],
            executable=_is_executable(name, limits["elevated_extensions"]),
            approved=verdict,
            elevated_requested=bool(elevated),
        )

    # -- fetching -------------------------------------------------------

    async def fetch(
        self,
        decision: PermissionDecision,
        write_decision: PermissionDecision,
        session_id: str,
        url: str,
        plan: Optional[DownloadPlan] = None,
        elevated: bool = False,
    ) -> Dict[str, Any]:
        """Fetch a URL into quarantine.

        Args:
            decision: An allowed ``download_files`` decision for this URL.
            write_decision: An allowed ``write_files`` decision whose target is
                :attr:`DownloadPlan.destination`. Separate because the fetch and
                the write are separate acts on separate things.
            session_id: The session, whose cookies the request carries.
            url: What to download.
            plan: A plan from :meth:`plan`, reused so the path the decision covers
                is the path the bytes land on.
            elevated: The user explicitly asked for an executable.

        Returns:
            ``path``, ``filename``, ``bytes``, ``sha256``, ``content_type``,
            ``quarantined`` and ``classification``.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: A decision is for another category or path.
            DryRunRefused: The decision describes rather than performs.
            KillSwitchRefused: The stop is engaged.
            SessionRefused: The session is gone.
            BlockedDomainRefused: The URL is not reachable at any trust level.
            DomainConfirmationRequired: The URL's domain is unapproved.
            ExecutableDownloadRefused: The file is executable and the user did not
                ask for that specifically.
            DownloadTooLarge: The file is over the cap.
            QuarantineRefused: The bytes could not be placed in the holding pen.
        """
        self._verify(decision, DOWNLOAD_CATEGORY)
        self._verify(write_decision, WRITE_CATEGORY)
        if self.kill_switch.is_killed():
            raise KillSwitchRefused("kill switch engaged; nothing will be downloaded")
        if self.guard is None:
            raise QuarantineRefused(
                "the download handler has no URL guard, so it will not fetch; "
                "wire one before downloading anything"
            )
        session = self.controller.get(session_id)
        current = plan or self.plan(url, elevated=elevated)
        verdict = self.guard.verify(
            decision, current.url, DOWNLOAD_CATEGORY,
            approved=session.trusted_domains,
            trust_level=decision.trust_level,
        )
        if verdict.blocked:
            raise BlockedDomainRefused(verdict.reason)
        if current.executable and not (elevated and current.elevated_requested):
            raise ExecutableDownloadRefused(
                f"{current.filename} is executable or script-shaped "
                f"({Path(current.filename).suffix.lower()}). ZUZZ will not fetch "
                f"one unless you specifically ask for an executable."
            )
        response = await session.context.request.get(current.url)
        declared = response.headers.get("content-length")
        size = _as_int(declared)
        if size is not None and size > current.max_bytes:
            await _discard(response)
            raise DownloadTooLarge(
                f"{current.filename} is {size} bytes, over the "
                f"{current.max_bytes // 1_048_576} MB download cap"
            )
        data = await response.body()
        await _discard(response)
        if len(data) > current.max_bytes:
            raise DownloadTooLarge(
                f"{current.filename} is {len(data)} bytes, over the "
                f"{current.max_bytes // 1_048_576} MB download cap. The server's "
                f"Content-Length did not say so."
            )
        if not data:
            raise QuarantineRefused(
                f"{current.url} returned no bytes; nothing was written"
            )
        content_type = str(response.headers.get("content-type") or "")
        destination = Path(current.destination)
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(NESTED_IGNORE, encoding="utf-8")
        try:
            # Fingers owns the write: it re-checks the kill switch, classifies the
            # path and makes a backup if something is already there. Nothing in
            # this package calls ``open()`` on a download. The mode is ``"w"``,
            # not ``"wb"`` — Fingers opens in binary itself and takes bytes or text
            # content, so an explicit ``b`` is a mode it refuses rather than a
            # stronger guarantee.
            self.file_manager.write(
                write_decision, str(destination), data, mode="w"
            )
        except Exception as exc:
            raise QuarantineRefused(
                f"the download could not be placed in quarantine at "
                f"{destination}: {exc}. Nothing was written anywhere else."
            ) from exc
        digest = hashlib.sha256(data).hexdigest()
        classification = self.classify(current.filename, content_type, data)
        session.touch()
        self._log(
            "browser_download_quarantined",
            session=session_id,
            url=current.url,
            file=current.filename,
            bytes=len(data),
            sha256=digest[:16],
            executable=classification["executable"],
        )
        return {
            "path": str(destination),
            "filename": current.filename,
            "bytes": len(data),
            "sha256": digest,
            "content_type": content_type,
            "quarantined": True,
            "classification": classification,
            "url": current.url,
        }

    # -- releasing ------------------------------------------------------

    async def release(
        self,
        decision: PermissionDecision,
        write_decision: PermissionDecision,
        path: str,
        destination: str,
    ) -> Dict[str, Any]:
        """Move a confirmed file out of quarantine.

        Args:
            decision: An allowed ``download_files`` decision.
            write_decision: An allowed ``write_files`` decision covering the
                destination, which is the act that actually puts the file
                somewhere the user chose.
            path: The quarantined file, which must be one.
            destination: Where it should go.

        Returns:
            ``from``, ``to``, ``bytes`` and the classification it was released
            under.

        Raises:
            QuarantineRefused: The source is not in the quarantine directory.
            DecisionRefused: No decision, or one that does not allow this.
        """
        self._verify(decision, DOWNLOAD_CATEGORY)
        self._verify(write_decision, WRITE_CATEGORY)
        source = Path(str(path)).resolve()
        if source.parent != self.root.resolve():
            raise QuarantineRefused(
                f"{path} is not in the quarantine directory, so there is nothing "
                f"to release. Only {self.root} holds unreviewed downloads."
            )
        if not source.is_file():
            raise QuarantineRefused(f"{source.name} is not in quarantine any more")
        # Fingers owns the move, so the destination is classified by the same code
        # that classifies every other write ZUZZ performs.
        moved = self.file_manager.move(write_decision, str(source), str(destination))
        self._log("browser_download_released", file=source.name,
                  destination=str(destination), moved=bool(moved))
        return {
            "from": str(source),
            "to": str(destination),
            "bytes": source.stat().st_size if source.is_file() else 0,
            "moved": bool(moved),
            "classification": self.classify(source.name, "", None),
        }

    def classify(
        self, filename: str, content_type: str = "", data: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """Classify a file without acting on it. Not gated; a gauge.

        Args:
            filename: The file name.
            content_type: What the server said it was.
            data: The bytes, when they are already in hand, so the MIME type can be
                sniffed rather than trusted.

        Returns:
            ``executable``, ``extension``, ``mime_guess``, ``category`` and
            ``requires_elevated``. ``executable`` is an *extension* verdict and
            says so — a renamed installer is not caught here, which is exactly why
            the file stays in a holding pen until a human opens it.
        """
        limits = self.limits()
        extension = Path(filename).suffix.lower()
        guessed = mimetypes.guess_type(filename)[0] or ""
        if data:
            guessed = _sniff(data) or content_type or guessed
        return {
            "executable": _is_executable(filename, limits["elevated_extensions"]),
            "extension": extension,
            "mime_guess": guessed,
            "declared_type": content_type,
            "category": _category(extension),
            "requires_elevated": _is_executable(
                filename, limits["elevated_extensions"]
            ),
            "basis": "file extension only; contents are not inspected",
        }

    def list_quarantine(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List what is in the holding pen. Not gated; a gauge.

        Names files and sizes them. Reading one is a separate act with its own
        decision, and moving one out is a third.

        Args:
            limit: How many entries to return, newest first.

        Returns:
            One record per file: ``name``, ``bytes``, ``received`` and
            ``classification``.
        """
        if not self.root.is_dir():
            return []
        entries: List[Dict[str, Any]] = []
        for path in self.root.iterdir():
            if not path.is_file() or path.name == ".gitignore":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": path.name,
                    "bytes": stat.st_size,
                    "received": round(stat.st_mtime, 3),
                    "classification": self.classify(path.name),
                }
            )
        return sorted(entries, key=lambda item: item["received"], reverse=True)[:limit]

    def ensure_dir(self) -> None:
        """Create the quarantine directory and its nested ignore rule.

        Returns:
            Nothing.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(NESTED_IGNORE, encoding="utf-8")

    # -- internals ------------------------------------------------------

    def _verify(self, decision: PermissionDecision, category: str) -> None:
        """Refuse the call unless the decision permits it.

        Args:
            decision: What the caller was handed.
            category: The action category this method performs.

        Returns:
            Nothing.

        Raises:
            DecisionRefused: No decision, or one that does not allow this.
            DecisionReplayRefused: The decision is for another category.
            DryRunRefused: The decision describes rather than performs.
        """
        if not isinstance(decision, PermissionDecision):
            raise DecisionRefused(
                "refusing to download without a PermissionDecision"
            )
        if decision.dry_run:
            raise DryRunRefused(
                "dry-run decisions describe the action; they never fetch anything"
            )
        if decision.blocked or not decision.allowed:
            raise DecisionRefused(
                f"the permission decision is {decision.decision}: {decision.reason}"
            )
        if str(decision.action.category) != str(category):
            raise DecisionReplayRefused(
                f"decision was for {decision.action.category}, not {category}"
            )

    def _log(self, action: str, **fields: Any) -> None:
        """Record an action in the activity log, when wired.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail. The URL and the size, never the
                bytes.

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


def _settings(guard: Optional[Any]) -> Dict[str, Any]:
    """Return the ``downloads`` block of the allowlist.

    Args:
        guard: The URL guard, or None.

    Returns:
        The configured policy, or an empty mapping when it cannot be read.
    """
    if guard is None:
        return {}
    config = guard.config()
    settings = config.get("downloads", {}) if isinstance(config, dict) else {}
    return settings if isinstance(settings, dict) else {}


def _unverified(url: str) -> Any:
    """Return a verdict standing in for one from an absent guard.

    Args:
        url: The URL.

    Returns:
        A verdict that is neither allowed nor blocked, so a caller that skipped the
        guard cannot mistake its absence for an approval.
    """
    from body.legs.url_guard import Verdict

    return Verdict(
        url=str(url),
        domain="",
        allowed=False,
        blocked=False,
        needs_confirmation=True,
        reason="no URL guard is wired, so this URL has not been checked",
        source="",
    )


def _name_from_url(url: str) -> str:
    """Return the file name a URL implies.

    Args:
        url: The URL.

    Returns:
        The last path segment, or an empty string.
    """
    from urllib.parse import unquote, urlsplit

    try:
        path = urlsplit(str(url)).path
    except ValueError:
        return ""
    segment = unquote(path.rsplit("/", 1)[-1]) if path else ""
    return segment.strip()


def _safe_name(name: str) -> str:
    """Return a name that is safe to write inside the quarantine directory.

    Args:
        name: The candidate, which comes from a URL and is therefore
            attacker-chosen.

    Returns:
        A name with no separators, no control characters, no reserved Windows
        device name and a length cap. Falls back to ``download`` when nothing
        usable is left.

    The extension is preserved where one survives sanitising, because the
    executable check reads it — sanitising ``.exe`` away would let a download be
    approved as a harmless text file.
    """
    cleaned = UNSAFE_NAME.sub("_", str(name)).strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned or cleaned.strip("._") == "":
        return "download"
    stem, extension = os.path.splitext(cleaned)
    stem = stem.strip(" .") or "download"
    extension = extension[:16]
    if stem.upper() in RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) > 100:
        stem = stem[:100]
    return f"{stem}{extension}"


def _is_executable(filename: str, extensions: List[str]) -> bool:
    """Return True when a file's extension is on the elevated list.

    Args:
        filename: The file name.
        extensions: The configured list.

    Returns:
        True when the extension matches. Extension only, and
        :meth:`DownloadHandler.classify` says so in its ``basis`` field, because a
        renamed installer is not caught here.
    """
    suffix = Path(str(filename)).suffix.lower()
    return bool(suffix) and suffix in {str(item).lower() for item in extensions}


def _category(extension: str) -> str:
    """Return a coarse category for a file, for the UI.

    Args:
        extension: The lower-cased extension, including the dot.

    Returns:
        One of ``executable``, ``archive``, ``document``, ``image``, ``media``,
        ``code`` or ``other``.
    """
    table = {
        "executable": (".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1",
                       ".vbs", ".js", ".jar", ".hta", ".lnk", ".reg", ".dll"),
        "archive": (".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".tar",
                    ".whl", ".iso", ".img", ".cab"),
        "document": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                     ".txt", ".md", ".csv", ".rtf", ".odt"),
        "image": (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
                  ".tiff"),
        "media": (".mp3", ".mp4", ".wav", ".ogg", ".webm", ".mov", ".avi", ".flac"),
        "code": (".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".html",
                 ".css", ".c", ".h", ".cpp", ".rs", ".go", ".sh"),
    }
    for name, suffixes in table.items():
        if extension in suffixes:
            return name
    return "other"


def _sniff(data: bytes) -> str:
    """Return a MIME type guessed from a file's first bytes.

    Args:
        data: The start of the file.

    Returns:
        The MIME type, or an empty string when nothing matches. A deliberately
        short list: the point is to notice that a ``.txt`` is a PE executable, not
        to build a full magic-number database.
    """
    head = data[:16]
    table = (
        (b"MZ", "application/x-msdownload"),
        (b"\x7fELF", "application/x-elf"),
        (b"PK\x03\x04", "application/zip"),
        (b"%PDF", "application/pdf"),
        (b"\x89PNG", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF8", "image/gif"),
        (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),
        (b"Rar!", "application/vnd.rar"),
        (b"\x1f\x8b", "application/gzip"),
        (b"7z\xbc\xaf", "application/x-7z-compressed"),
    )
    for magic, mime in table:
        if head.startswith(magic):
            return mime
    return ""


def _as_int(value: Any) -> Optional[int]:
    """Return an integer, or None when the value is not one.

    Args:
        value: The candidate.

    Returns:
        The integer, or None.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _discard(response: Any) -> None:
    """Release a response body without reading it.

    Args:
        response: The Playwright API response.

    Returns:
        Nothing. Best-effort: a response that cannot be discarded will be collected
        when the object goes out of scope anyway.
    """
    try:
        await response.dispose()
    except Exception:
        pass