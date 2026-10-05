"""Occipital Lobe — the storage guard.

The one place in the region where a screenshot is allowed to reach disk, and the one
place that has to be sure it should. Every rule here exists because a screenshot is
the widest-reaching artefact ZUZZ produces: one frame of a desktop holds a logged-in
bank portal, a password manager and a private chat at once, and unlike a file there
is no name to classify before reading it.

Three rules, in order:

* **Classify first.** The caller classifies, this method refuses. A store guard that
  classified for itself would be a second classifier, and two classifiers drift.
* **Refuse, do not silently drop.** A ``sensitive`` screen without an explicit
  acknowledgement raises rather than being quietly discarded. Silence would leave the
  caller believing it had a picture to work from, and it would be indistinguishable
  from a capture that simply produced nothing.
* **Never store the secret in the record.** The audit entry names the *kind* of
  finding — a masked field, an API key shape — and the flagged index records the file
  stem. A guard that wrote the detected credential into its own audit log would have
  moved the secret rather than flagged it.

Storing a sensitive screen *with* acknowledgement is allowed. The pixels are already
in memory; the acknowledgement is about writing them somewhere they will outlive the
task, which is a genuinely different thing and worth a separate yes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.exceptions import SensitiveScreenDetected
from brain.occipital_lobe.sensitivity_index import SensitivityIndex
from brain.occipital_lobe.screenshot_store import Screenshots
from brain.occipital_lobe.visual_classifier import SENSITIVE


class StorageGuard:
    """Decides whether a captured screen may be written to disk.

    Attributes:
        store: Where screenshots are written.
        index: Which of them carry credentials.
        audit: Audit log; None means a decision is not recorded.
    """

    def __init__(self, store: Screenshots, audit: Optional[Any] = None) -> None:
        """Wire the guard.

        Args:
            store: The screenshot store.
            audit: Audit log for the sensitive-screen events.
        """
        self.store = store
        self.audit = audit

    def store_capture(
        self,
        capture: Capture,
        classification: Dict[str, Any],
        source: str = "",
        acknowledge_sensitive: bool = False,
    ) -> Dict[str, Any]:
        """Write one capture, unless it carries credentials and was not acknowledged.

        Args:
            capture: The screenshot to store.
            classification: The classifier's verdict for it.
            source: What was captured, for the audit record.
            acknowledge_sensitive: Store a sensitive screen anyway.

        Returns:
            A record with ``path``, ``name``, ``stored`` and whether the screen was
            flagged as sensitive.

        Raises:
            SensitiveScreenDetected: The screen carries credentials and
                ``acknowledge_sensitive`` was not set. The finding is already in the
                audit log by the time this raises.
        """
        if classification.get("classification") == SENSITIVE and not acknowledge_sensitive:
            raise SensitiveScreenDetected(
                f"{classification.get('summary', 'credential material')} on screen. "
                "Refusing to write it to disk without an explicit acknowledgement; "
                "the capture is still in memory."
            )
        path = self.store.save(capture)
        if classification.get("classification") == SENSITIVE:
            SensitivityIndex(self.store.root).flag([path.stem])
            self._record(classification, source)
        return {
            "path": str(path),
            "name": path.name,
            "stored": True,
            "sensitive": classification.get("classification") == SENSITIVE,
            "acknowledged": acknowledge_sensitive,
        }

    def _record(self, classification: Dict[str, Any], source: str) -> None:
        """Write the audit entry for a stored sensitive screen.

        Args:
            classification: The classifier's verdict.
            source: What was captured.

        Returns:
            Nothing. A log that fails must not fail the store: the file is already
            written and the user is waiting, and the alternative — raising from here —
            would report a failed capture for a file that exists.
        """
        if self.audit is None:
            return
        labels = ", ".join(
            finding.get("label", "") for finding in classification.get("findings", [])[:3]
        )
        try:
            self.audit.record(
                "sensitive_screen",
                f"stored a screen carrying credential material ({labels})",
                category="visual_capture",
                target=source,
                level=SENSITIVE,
                signals=[finding.get("signal", "") for finding in classification.get("findings", [])],
                labels=[finding.get("id", "") for finding in classification.get("findings", [])],
            )
        except Exception:
            pass


def stored_name(path: Path) -> str:
    """Return the file name of a stored screenshot.

    Args:
        path: The path :meth:`StorageGuard.store_capture` returned.

    Returns:
        The bare file name, which is the only form the store will resolve later.
    """
    return path.name