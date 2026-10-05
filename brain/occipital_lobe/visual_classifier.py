"""Occipital Lobe — what is on this screen, before it is stored.

A screenshot is the widest-reaching read ZUZZ performs: one frame of a desktop holds a
logged-in bank portal, a password manager, an email inbox and a private chat at once,
and there is no filename to classify beforehand. So the classifier runs *before*
storage, every time, and a frame carrying credential material is not written to disk
unless the user explicitly says so.

The signals themselves — the masked-input scan, the key shapes, the keywords and the
three-way verdict — are in :mod:`brain.occipital_lobe.sensitivity_signals`. What is
here is the object that collects them, consults the model, and writes the audit
record.

Three things this class is careful about:

* **It only ever raises a classification.** The model's opinion can take a screen from
  ``safe`` to ``caution`` or ``sensitive``. It can never lower one, because a small
  model answering "no" about a password manager is not evidence that there is none.
* **It never records the secret.** The findings name the *kind* of thing found —
  ``"an API key (sk-…)"``, ``"a masked field (12 characters hidden)"`` — and never
  its value. A classifier whose audit record contains the credential it detected has
  moved the secret rather than flagged it. The one place a value could plausibly
  re-enter is the model's own evidence line, and that goes through
  :func:`~brain.occipital_lobe.sensitivity_signals.safe_evidence`, which either
  returns a credential-free phrase or withholds the evidence entirely.
* **It fails safe.** :meth:`VisualClassifier.ask_model` returns an empty result when
  the model cannot be reached, rather than raising, because a classification is an
  input to a storage decision and an unreachable model must not become the reason a
  sensitive screen reaches disk.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from body.hands.text_scanner import looks_like_credential

from brain.occipital_lobe import sensitivity_signals as signals
from brain.occipital_lobe.untrusted_text import UntrustedText
from brain.occipital_lobe.vision_client import VisionClient

#: The three verdicts, re-exported from the signal module so a caller does not have
#: to know which of the two modules it is meant to import from.
SAFE = signals.SAFE
CAUTION = signals.CAUTION
SENSITIVE = signals.SENSITIVE


class VisualClassifier:
    """Decides whether a captured screen carries credential material.

    Attributes:
        client: The vision client, used for the model-side signal. Optional, because
            the visual and textual signals need no model at all — which is what lets
            the storage guard work when Moondream cannot be loaded.
        audit: Audit log; None means a finding is not recorded.
        logger: Activity logger; None means nothing is logged.
    """

    def __init__(
        self,
        client: Optional[VisionClient] = None,
        audit: Optional[Any] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the classifier.

        Args:
            client: The vision client for the ``sensitive`` task.
            audit: Audit log for ``sensitive_screen`` events.
            logger: Activity logger.
        """
        self.client = client
        self.audit = audit
        self.logger = logger

    def scan(
        self,
        image: Optional[bytes] = None,
        text: Optional[UntrustedText] = None,
    ) -> Dict[str, Any]:
        """Classify a screen from what is already known about it.

        Costs nothing and needs no model: the OCR text if there is any, plus a scan of
        the pixels. This is the signal set that runs before storage, where an extra
        model call would be a model call on a screen that may itself be a credential.

        Args:
            image: The screenshot bytes. Accepted for symmetry with
                :meth:`ask_model` and for future visual signals; the masked-input
                signal reads the OCR text, because a mask is only recognisable once
                something has read the pixels.
            text: Whatever the OCR reader produced.

        Returns:
            A record with ``classification``, ``findings`` and a summary.
        """
        body = text.cleaned() if text is not None and not text.is_empty else ""
        found: List[Dict[str, str]] = []
        found.extend(signals.mask_runs(body))
        found.extend(signals.key_shapes(body))
        credential = looks_like_credential(body) if body else None
        if credential:
            found.append(
                {
                    "id": credential["id"],
                    "label": credential["label"],
                    "signal": "text",
                    "severity": SENSITIVE,
                }
            )
        words = signals.keywords(body)
        if words:
            found.append(
                {
                    "id": "credential_keyword",
                    "label": f"mentions {', '.join(words[:3])}",
                    "signal": "text",
                    "severity": CAUTION,
                }
            )
        return signals.verdict(found)

    async def ask_model(self, image: bytes) -> Dict[str, Any]:
        """Ask the vision model whether the screen looks like a credential surface.

        The signal that catches what OCR misses: a password manager whose fields
        render as solid blocks rather than bullets, or a banking app with no readable
        text at all.

        Args:
            image: The screenshot bytes.

        Returns:
            A record with ``classification`` and ``findings``; empty when the model
            could not be reached or said no. Never raises.
        """
        if self.client is None:
            return signals.verdict([])
        try:
            reply = await self.client.ask("sensitive", [image])
        except Exception:
            return signals.verdict([])
        if not signals.MODEL_YES.search(reply.text):
            return signals.verdict([])
        evidence = reply.text.split("EVIDENCE", 1)
        # Redacted, and the redaction stated in the label. The model was shown the
        # screen and asked what is on it, so its evidence is the single most likely
        # path for a real credential to reach the audit log -- a small model asked to
        # describe a login form will quote the password back. `safe_evidence` makes
        # the stronger promise this class needs: whatever comes back holds no
        # credential value, and anything it cannot vouch for is withheld outright
        # rather than quoted. Naming the redaction keeps the record honest about
        # having removed something.
        cleaned = signals.safe_evidence(evidence[1]) if len(evidence) > 1 else ""
        label = cleaned or "credential surface (the model gave no usable evidence)"
        if "<redacted:" in label:
            label = f"{label} -- credential value withheld"
        return signals.verdict(
            [
                {
                    "id": "model_evidence",
                    "label": label,
                    "signal": "model",
                    "severity": SENSITIVE,
                }
            ]
        )

    def combine(self, *records: Dict[str, Any]) -> Dict[str, Any]:
        """Merge several classifications into the most concerned answer.

        Args:
            *records: Classifications from :meth:`scan` and :meth:`ask_model`.

        Returns:
            One record carrying every finding. The most concerned classification wins
            and a single finding is enough to reach it: this is a detector, not a
            vote, and a majority that includes one masked field is not a majority.
        """
        found: List[Dict[str, str]] = []
        for record in records:
            found.extend(record.get("findings", []))
        return signals.verdict(found)

    def record(self, result: Dict[str, Any], source: str = "") -> Dict[str, Any]:
        """Write a sensitive-screen finding to the audit log.

        Args:
            result: The classification.
            source: What was classified, e.g. ``"capture:monitor=0"``.

        Returns:
            The classification, unchanged, so this chains onto the call that produced
            it.
        """
        if self.audit is None or result.get("classification") != SENSITIVE:
            return result
        labels = ", ".join(finding["label"] for finding in result.get("findings", [])[:3])
        self.audit.record(
            "sensitive_screen",
            f"captured screen carries credential material ({labels})",
            category="visual_capture",
            target=source,
            level=SENSITIVE,
            signals=[finding["signal"] for finding in result.get("findings", [])],
            labels=[finding["id"] for finding in result.get("findings", [])],
        )
        return result