"""Occipital Lobe — screenshot store.

Writes captures under ``storage/screenshots/`` and hands back only files that are
genuinely in there. Split out of :mod:`brain.occipital_lobe.screen_capture` because
this is the half of capture that touches the filesystem, and the half that has to
be right about three separate things:

* **Unique names.** Timestamped to the second, numbered per process. A timestamp
  alone is not uniqueness: two captures in the same second would overwrite, and the
  second frame is frequently the one being compared against the first.
* **Untracked by construction.** The repository ``.gitignore`` covers the
  directory, and the store also writes a ``.gitignore`` inside it on first use.
  The two answer different questions — "do not commit these today" versus "these
  were never committable" — so a later tidy-up of the repository rule cannot
  quietly start committing frames of the user's desktop.
* **No traversal.** :meth:`Screenshots.resolve` accepts a bare file name, refuses
  anything containing a separator, and then confirms the resolved path's parent is
  the store itself. The name check alone is enough for every caller in the
  codebase; the parent check is what makes it enough for the next one.
"""

from __future__ import annotations

import itertools
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.exceptions import SensitiveScreenDetected
from brain.occipital_lobe.sensitivity_index import SensitivityIndex
from core.config import ROOT

#: Where screenshots are written.
SCREENSHOT_DIR = ROOT / "storage" / "screenshots"

#: Dropped into the screenshot directory the first time it is created.
NESTED_IGNORE = (
    "# Captured screens are frames of the user's desktop. Never commit them.\n"
    "#\n"
    "# The repository .gitignore covers this directory too. Both are here on\n"
    "# purpose: that rule can be tidied away by accident, this one is local to\n"
    "# the directory it protects.\n"
    "*\n"
    "!.gitignore\n"
)

#: Per-process counter, so two captures in the same second cannot collide.
_COUNTER = itertools.count(1)

_UNSAFE = re.compile(r"[^a-z0-9]+")


class Screenshots:
    """The screenshot directory.

    Attributes:
        root: The directory screenshots live in.
        logger: Activity logger; None means nothing is logged.
    """

    def __init__(self, root: Optional[Path] = None, logger: Optional[Any] = None) -> None:
        """Open the store.

        Args:
            root: Override for the directory, used by tests so a run never
                writes into the developer's own storage.
            logger: Activity logger.
        """
        self.root = Path(root) if root else SCREENSHOT_DIR
        self.logger = logger

    def save(self, capture: Capture) -> Path:
        """Write one capture.

        Args:
            capture: The screenshot to store.

        Returns:
            The absolute path written.
        """
        self.ensure_dir()
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(capture.captured_at))
        path = self.root / f"{stamp}-{next(_COUNTER):04d}-{_slug(capture.label)}.png"
        path.write_bytes(capture.image)
        self._log("screenshot_saved", path=str(path), bytes=len(capture.image))
        return path

    def ensure_dir(self) -> None:
        """Create the directory and its nested ignore rule if they are missing.

        Returns:
            Nothing. A failure to create the directory raises from
            :meth:`mkdir`, which is where the caller needs to hear about it.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(NESTED_IGNORE, encoding="utf-8")

    def resolve(self, name: str) -> Path:
        """Return the path of one stored screenshot.

        Args:
            name: A bare file name, as returned by :meth:`save`.

        Returns:
            The resolved absolute path.

        Raises:
            SensitiveScreenDetected: The name does not name a stored screenshot.
                Named for what it is — content outside the store — rather than for
                the traversal it usually means, so a caller that catches it does
                not need to know the failure mode to handle it.
        """
        candidate = Path(str(name)).name
        if candidate != str(name) or candidate in ("", ".", ".."):
            raise SensitiveScreenDetected(
                f"{name!r} is not a stored screenshot: only a bare file name is accepted"
            )
        root = self.root.resolve()
        resolved = (root / candidate).resolve()
        if resolved.parent != root or not resolved.is_file():
            raise SensitiveScreenDetected(f"{name!r} is not a stored screenshot")
        return resolved

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the stored screenshots, newest first.

        Listing is not gated. It names files in ZUZZ's own storage, which is the
        same class of read as the trust level; what is *inside* those files is
        gated, which is where the privacy question actually lives.

        Args:
            limit: How many to return at most.

        Returns:
            One record per PNG: ``name``, ``bytes``, ``captured_at`` and
            ``sensitive``, the last read from
            :class:`~brain.occipital_lobe.sensitivity_index.SensitivityIndex`.
        """
        if not self.root.is_dir():
            return []
        flagged = SensitivityIndex(self.root).flagged()
        entries: List[Dict[str, Any]] = []
        for path in self.root.glob("*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": path.name,
                    "bytes": stat.st_size,
                    "captured_at": round(stat.st_mtime, 3),
                    "sensitive": path.stem in flagged,
                }
            )
        return sorted(entries, key=lambda item: item["captured_at"], reverse=True)[:limit]

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail.

        Returns:
            Nothing. A logger that raises must not turn a stored screenshot into a
            failed call, so its errors are swallowed.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("occipital_lobe", action, **fields)
        except Exception:
            pass


def _slug(label: str) -> str:
    """Return a filename-safe form of a label.

    The label reaches the filesystem, so it is treated as untrusted input even
    though it usually comes from ZUZZ itself.

    Args:
        label: The caller's label.

    Returns:
        Lower-case alphanumerics and dashes, or ``capture`` when nothing usable is
        left.
    """
    collapsed = "-".join(part for part in _UNSAFE.split(str(label).lower()) if part)
    return collapsed[:40] or "capture"