"""Occipital Lobe — the orchestrator.

The high-level API every other region and the HTTP layer talk to. Its shape is one
rule: **every public method takes a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` as its first
argument**, and the target that decision names must match what the method is about to
do. A screenshot is the most privacy-exposing read ZUZZ performs, so the gate is the
signature rather than a convention inside the body of a method.

The six capabilities, in the order a computer-use agent needs them:

* :meth:`OccipitalLobe.capture` — take a screenshot.
* :meth:`OccipitalLobe.capture_and_store` — take one, classify it, and write it to
  disk only if that is safe.
* :meth:`OccipitalLobe.describe` — say what is on the screen.
* :meth:`OccipitalLobe.find_element` — turn "the Search bar" into a coordinate.
* :meth:`OccipitalLobe.read_text` — transcribe a region.
* :meth:`OccipitalLobe.compare` — did the screen change between two captures.

The three model-backed methods delegate to
:class:`~brain.occipital_lobe.lobe_vision.LobeVision`, which is where the VRAM check
lives. What this class owns is the region's invariants:

* **The gate is the signature**, checked twice: once here for the operation, and once
  inside the collaborator for the same operation against the same target. A stop that
  engages between the two checks is caught by the second.
* **Screenshots are classified before they are stored.**
  :meth:`capture_and_store` classifies and hands the verdict to
  :class:`~brain.occipital_lobe.storage_guard.StorageGuard`, which refuses a
  ``sensitive`` frame without an explicit acknowledgement and records it either way.
* **Screen text never becomes reasoning.** Every method that returns model text
  returns it inside :class:`~brain.occipital_lobe.untrusted_text.UntrustedText`, and
  every prompt is built by :mod:`brain.occipital_lobe.vision_prompts`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.occipital_lobe import lobe_status, vision_guards
from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.capture_backend import CaptureBackend
from brain.occipital_lobe.element_finder import ElementFinder
from brain.occipital_lobe.exceptions import ElementNotFound
from brain.occipital_lobe.lobe_vision import LobeVision
from brain.occipital_lobe.ocr_reader import OcrReader
from brain.occipital_lobe.screenshot_store import Screenshots
from brain.occipital_lobe.screen_capture import ScreenCapture
from brain.occipital_lobe.storage_guard import StorageGuard
from brain.occipital_lobe.visual_classifier import VisualClassifier
from brain.occipital_lobe.visual_verifier import verify
from brain.occipital_lobe.vision_client import VisionClient

#: Re-exported so a caller can catch it after importing only the orchestrator.
__all__ = ["ElementNotFound", "OccipitalLobe"]


class OccipitalLobe:
    """Screen vision, behind the permission gate.

    Attributes:
        engine: The screenshot engine. Named ``engine`` and not ``capture``
            because an attribute called ``capture`` shadows
            :meth:`OccipitalLobe.capture` — the instance attribute wins over the
            class method, so ``lobe.capture(...)`` would silently resolve to the
            engine object and raise ``'ScreenCapture' object is not callable``
            instead of taking a screenshot.
        client: The Moondream client.
        vision: The three model-backed operations.
        classifier: Sensitive-content classification.
        guard: The storage guard.
        hypothalamus: The resource layer, consulted before any model loads.
        kill_switch: Re-checked on every call.
    """

    def __init__(
        self,
        capture: Optional[ScreenCapture] = None,
        client: Optional[VisionClient] = None,
        finder: Optional[ElementFinder] = None,
        ocr: Optional[OcrReader] = None,
        classifier: Optional[VisualClassifier] = None,
        guard: Optional[StorageGuard] = None,
        hypothalamus: Optional[Any] = None,
        backend: Optional[CaptureBackend] = None,
        store: Optional[Screenshots] = None,
        audit: Optional[Any] = None,
        logger: Optional[Any] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        """Wire the region together.

        Every collaborator is injectable, which is what lets the whole region be
        exercised with no display, no GPU and no Ollama — the only way the safety
        paths are testable on a machine that is not the target machine.

        Args:
            capture: The screenshot engine.
            client: The Moondream client.
            finder: Element location.
            ocr: Text reading.
            classifier: Sensitive-content classification.
            guard: The storage guard.
            hypothalamus: The Hypothalamus asked before the model loads.
            backend: Capture backend, used only when ``capture`` is not given.
            store: Screenshot store, used only when ``capture`` is not given.
            audit: Audit log.
            logger: Activity logger.
            kill_switch: The switch to re-check, or the process-wide one.
        """
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.audit = audit
        self.logger = logger
        self.client = client if client is not None else VisionClient()
        self.engine = capture if capture is not None else ScreenCapture(
            backend=backend, store=store, kill_switch=self.kill_switch, logger=logger
        )
        self.finder = finder if finder is not None else ElementFinder(
            self.client, kill_switch=self.kill_switch, logger=logger
        )
        self.ocr = ocr if ocr is not None else OcrReader(
            self.client, kill_switch=self.kill_switch, logger=logger
        )
        self.classifier = classifier if classifier is not None else VisualClassifier(
            self.client, audit=audit, logger=logger
        )
        self.guard = guard if guard is not None else StorageGuard(self.engine.store, audit)
        self.hypothalamus = hypothalamus
        self.vision = LobeVision(
            self.engine,
            self.client,
            self.finder,
            self.ocr,
            hypothalamus=hypothalamus,
            audit=audit,
            kill_switch=self.kill_switch,
        )

    # -- capture --------------------------------------------------------------

    def capture(
        self,
        decision: PermissionDecision,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
        label: str = "",
    ) -> Capture:
        """Take one screenshot without storing it.

        Storing is deliberately a separate, gated step. "Look at the screen" and
        "keep a copy of the screen" are different permissions, and a user who approved
        the first has not approved the second.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``capture``.
            monitor: Zero-based monitor index.
            region: ``(x, y, width, height)`` in monitor-local pixels.
            label: Short name for the log.

        Returns:
            The capture.
        """
        return self.engine.capture(decision, monitor=monitor, region=region, label=label)

    async def capture_and_store(
        self,
        decision: PermissionDecision,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
        label: str = "",
        acknowledge_sensitive: bool = False,
        consult_model: bool = False,
    ) -> Dict[str, Any]:
        """Take a screenshot, classify it, and store it if that is safe.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``capture``.
            monitor: Zero-based monitor index.
            region: ``(x, y, width, height)`` in monitor-local pixels.
            label: Short name for the log and the stored file name.
            acknowledge_sensitive: Store a screen carrying credentials anyway. The
                pixels are in memory anyway; the acknowledgement is about writing them
                where they will outlive the task.
            consult_model: Also ask the vision model whether the screen looks like a
                credential surface. Catches what the local signals miss — a password
                manager drawing solid blocks rather than bullets. Opt-in, because the
                storage guard has to work when Moondream cannot be loaded at all.

        Returns:
            A record with the capture's metadata, the stored path, and the
            classification.

        Raises:
            SensitiveScreenDetected: The screen carries credentials and
                ``acknowledge_sensitive`` was not set. The finding is in the audit log
                either way.
            CropOutOfBounds: The region leaves the monitor.
        """
        capture = self.engine.capture(decision, monitor=monitor, region=region, label=label)
        source = vision_guards.target_for("capture", monitor, region)
        classification = self.classifier.scan(capture.image)
        if consult_model:
            classification = self.classifier.combine(
                classification, await self.classifier.ask_model(capture.image)
            )
        self.classifier.record(classification, source)
        stored = self.guard.store_capture(capture, classification, source, acknowledge_sensitive)
        return {**capture.summary(), **stored, "classification": classification}

    def monitors(self) -> List[Dict[str, Any]]:
        """Return every attached monitor. Not gated; a gauge, not an action."""
        return self.engine.monitors()

    def stored(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the stored screenshots, newest first. Not gated."""
        return self.engine.store.list(limit)

    # -- model-backed capabilities -------------------------------------------

    async def describe(
        self,
        decision: PermissionDecision,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
        question: str = "",
    ) -> Dict[str, Any]:
        """Say what is on the screen.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``describe``.
            monitor: Zero-based monitor index.
            region: ``(x, y, width, height)`` in monitor-local pixels.
            question: An optional focus, e.g. ``"what is this dialog asking"``.

        Returns:
            A record with the description as untrusted text, its confidence, and the
            threshold verdict.
        """
        return await self.vision.describe(decision, monitor, region, question)

    async def find_element(
        self,
        decision: PermissionDecision,
        description: str,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
    ) -> Dict[str, Any]:
        """Locate a UI element and return its coordinates.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``find``.
            description: What to look for, in the user's words.
            monitor: Zero-based monitor index.
            region: ``(x, y, width, height)`` in monitor-local pixels to search.

        Returns:
            The element record: status, derived confidence, the box in image pixels,
            the centre in image and desktop coordinates, and the normalised box.

        Raises:
            ElementNotFound: The model returned no usable box.
        """
        return await self.vision.find_element(decision, description, monitor, region)

    async def read_text(
        self,
        decision: PermissionDecision,
        region: Optional[Sequence[int]] = None,
        monitor: int = 0,
    ) -> Dict[str, Any]:
        """Transcribe the text in a region.

        Args:
            decision: An allowed ``visual_capture`` decision for an ``ocr``.
            region: ``(x, y, width, height)`` in **monitor-local** pixels, or None
                for the whole monitor.
            monitor: Zero-based monitor index.

        Returns:
            The OCR record, with the text marked untrusted.
        """
        return await self.vision.read_text(decision, region, monitor)

    # -- comparison and status ------------------------------------------------

    def compare(
        self,
        decision: PermissionDecision,
        before: Any,
        after: Any,
        label: str = "",
        monitor: int = 0,
    ) -> Dict[str, Any]:
        """Compare two captures and say whether the screen changed.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``verify``.
            before: A capture, image bytes, or a path to a stored screenshot.
            after: The same.
            label: What the comparison is about.
            monitor: Zero-based monitor index the ``verify`` decision was issued for.

        Returns:
            The comparison report.
        """
        self._verify("verify", decision, monitor, None)
        result = verify(before, after, label)
        self._log("visual_verify", changed=result["changed"], reason=result["reason"], label=label)
        return result

    def status(self) -> Dict[str, Any]:
        """Return the region's configuration for the UI.

        Returns:
            The thresholds, the prompt-isolation policy, the monitors, the model and
            budget state, and how many screenshots are stored. Nothing here needs a
            decision: it is the same class of read as the trust level.
        """
        return lobe_status.build(
            self.hypothalamus,
            self.monitors(),
            self.client.describe_prompts(),
            len(self.stored()),
        )

    # -- permissions ----------------------------------------------------------

    def propose(
        self,
        operation: str,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
    ) -> Action:
        """Build the action a caller must get decided before calling this region.

        Args:
            operation: One of ``capture``, ``describe``, ``find``, ``ocr``, ``verify``.
            monitor: Zero-based monitor index.
            region: ``(x, y, width, height)`` in monitor-local pixels, or None.

        Returns:
            The proposed action. Passing it through the gate is the caller's job — the
            region does not decide, it only states what it is about to do.
        """
        return Action(
            category=ActionCategory.VISUAL_CAPTURE,
            target=vision_guards.target_for(operation, monitor, region),
            detail={"operation": operation},
        )

    def propose_read(self, name: str) -> Action:
        """Build the action for reading one stored screenshot.

        Separate from :meth:`propose` because the target must differ from a live
        capture's: :func:`vision_guards.same_target` matches exactly, so a distinct
        spelling is what keeps an approval for one from covering the other.

        Args:
            name: The file name as returned by the store.

        Returns:
            The proposed action.
        """
        return Action(
            category=ActionCategory.VISUAL_CAPTURE,
            target=vision_guards.screenshot_target_for(name),
            detail={"operation": "read-screenshot"},
        )

    def _verify(
        self,
        operation: str,
        decision: Any,
        monitor: int,
        region: Optional[Sequence[int]],
    ) -> None:
        """Check the decision names this exact operation.

        Args:
            operation: The operation about to run.
            decision: What the caller was handed.
            monitor: The monitor it is about to act on.
            region: The region it is about to act on.

        Returns:
            Nothing. Raises rather than returning a verdict, matching the Body layers:
            the orchestrator does not branch on permissions.
        """
        vision_guards.verify_decision(
            decision, vision_guards.target_for(operation, monitor, region), self.kill_switch
        )

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one.

        Args:
            action: Short verb phrase.
            **fields: Extra structured detail.

        Returns:
            Nothing. Errors are swallowed so logging cannot fail a vision call.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("occipital_lobe", action, **fields)
        except Exception:
            pass