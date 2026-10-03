"""Body/Hands — the clipboard.

Reading and writing the clipboard, behind the gate.

**A read that looks like a secret is escalated, not confirmed by the caller.**
The clipboard is where credentials end up by accident — a password copied from a
password manager, a token from a dashboard. :func:`looks_like_credential`
recognises the common shapes, and a match reclassifies the read as
``access_credentials``, which asks at every trust level. The heuristics are
deliberately loose, because the two errors cost different amounts: a false
positive costs one confirmation, and a false negative writes a live credential
into the activity log, where it stays.

The heuristic result is never logged. What is logged is the *kind* of finding,
because a log that records "this looked like an API key" is useful and a log
that records the key is the exact failure this module exists to prevent.

**A write is checked for dangerous text too.** Setting the clipboard does not
type anything, so a refusal here would be wrong — but the string usually ends up
typed by whatever reads it next, so the finding is returned to the caller rather
than swallowed. The caller decides what a clipboard write costs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from body.hands import action_classifier
from body.hands.exceptions import InvalidInputRefused
from body.hands.hands_guards import verify_decision
from body.hands.text_scanner import looks_like_credential, scan_text


class Clipboard:
    """The system clipboard, behind the gate.

    Attributes:
        backend: Object with ``read()`` and ``write(text)``. PyAutoGUI's
            pyperclip-backed clipboard by default; a stub in tests.
    """

    def __init__(
        self,
        backend: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the clipboard to its backend.

        Args:
            backend: The clipboard implementation. Resolved lazily from
                PyAutoGUI when None, so importing this module never requires a
                display.
            kill_switch: The switch re-checked on every call.
            logger: Activity logger; None means no logging.
        """
        self._backend = backend
        self.kill_switch = kill_switch
        self.logger = logger

    @property
    def backend(self) -> Any:
        """Return the clipboard implementation, importing it on first use."""
        if self._backend is None:
            import pyautogui

            self._backend = pyautogui
        return self._backend

    def read(self, decision: Any) -> Dict[str, Any]:
        """Read the clipboard.

        The order here is deliberate and looks wrong at a glance, so: the
        clipboard is read *before* the decision is verified, because the content
        is what determines which category applies, and the category is part of
        what the decision must match. Reading it first is unavoidable.

        What makes it safe is that nothing leaves this function until the check
        passes. The text sits in a local, the decision is verified against the
        category the content demands, and the method either returns the text and
        logs a finding *kind*, or raises with the text discarded unread. A
        refused read costs one local assignment; it does not log a credential,
        return one, or transmit one.

        Args:
            decision: A ``read_files`` decision, or ``access_credentials`` when
                the caller expects a secret.

        Returns:
            The text, the category it was read under, and any credential
            finding. The finding is what lets the caller escalate and re-read
            with the right decision, so the content is not returned under the
            wrong category by accident.

        Raises:
            HandsRefused: The decision does not permit this read at the category
                the content requires.
        """
        text = self._read_text()
        finding = looks_like_credential(text)
        verify_decision(
            decision,
            action_classifier.clipboard_read_category(finding),
            str(decision.action.target),
            self.kill_switch,
        )
        self._log("clipboard_read", chars=len(text or ""), finding=_safe(finding))
        return {
            "text": text,
            "category": action_classifier.clipboard_read_category(finding),
            "credential_finding": finding,
        }

    def write(self, decision: Any, text: str) -> Dict[str, Any]:
        """Replace the clipboard contents.

        Args:
            decision: A ``write_files`` decision.
            text: The text to place on the clipboard.

        Returns:
            What was written and what it looks like. Dangerous-text findings are
            returned rather than raised: setting the clipboard types nothing, so
            refusing would be wrong, but the string usually gets typed by
            whatever reads it next.

        Raises:
            InvalidInputRefused: The text is not a string.
            HandsRefused: The decision does not permit this write.
        """
        if not isinstance(text, str):
            raise InvalidInputRefused(
                f"clipboard text must be a string, got {type(text).__name__}"
            )
        verify_decision(
            decision,
            action_classifier.category_for("clipboard_write"),
            str(decision.action.target),
            self.kill_switch,
        )
        self.backend.write(text)
        findings = scan_text(text)
        self._log("clipboard_write", chars=len(text), findings=len(findings))
        return {"written": len(text), "findings": findings}

    def clear(self, decision: Any) -> Dict[str, Any]:
        """Empty the clipboard.

        Counting as a write rather than a delete: nothing is destroyed on disk,
        and the user's copied text is usually still recoverable from the
        application they copied it in.

        Args:
            decision: A ``write_files`` decision.

        Returns:
            A record that the clipboard was emptied.
        """
        verify_decision(
            decision,
            action_classifier.category_for("clipboard_clear"),
            str(decision.action.target),
            self.kill_switch,
        )
        self.backend.write("")
        self._log("clipboard_clear")
        return {"cleared": True}

    def _read_text(self) -> str:
        """Return the clipboard contents as text, or an empty string.

        A clipboard holding an image or a file list has no text form. That is
        not an error and not a secret, so it reads as empty rather than raising.
        """
        try:
            value = self.backend.paste()
        except Exception:
            return ""
        return value if isinstance(value, str) else ""

    def _log(self, action: str, **fields: Any) -> None:
        """Record one clipboard action, never including the content."""
        if self.logger is not None:
            self.logger.log("hands", action, **fields)


def _safe(finding: Optional[Dict[str, str]]) -> str:
    """Return the finding's kind without its content.

    The clipboard may hold a live credential. Logging the finding's ``label``
    says "this looked like an API key", which is what an auditor needs, and
    cannot itself leak the secret.
    """
    return finding["id"] if finding else ""