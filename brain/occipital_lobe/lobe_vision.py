"""Occipital Lobe — the model-backed operations.

Everything in the region that asks Moondream a question: describe the screen, find an
element, read text. Split out of :mod:`brain.occipital_lobe.occipital_lobe` so that
the orchestrator stays about *the region's* invariants — permission on every entry,
the Hypothalamus before every model load, classify before every store — while the
three operations that do inference live together with their own gating.

The split is also what keeps the VRAM check honest. All three methods begin with
:meth:`LobeVision.admit`, one call to
:func:`brain.occipital_lobe.vision_resources.admit`. A method added to this class has
to decide whether to call it, and a method added anywhere else has to decide whether
to be in this class — which is a smaller question than "did you remember the budget?"
asked of every new vision method forever.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from brain.occipital_lobe import thresholds, vision_guards, vision_resources
from brain.occipital_lobe.element_finder import JOB as FIND_JOB
from brain.occipital_lobe.exceptions import ElementNotFound
from brain.occipital_lobe.ocr_engines import plausible_confidence
from brain.occipital_lobe.ocr_reader import JOB as OCR_JOB
from brain.occipital_lobe.screen_capture import ScreenCapture
from brain.occipital_lobe.vision_client import VisionClient

#: The job a screen description is judged against.
DESCRIBE_JOB = "describe_scene"


class LobeVision:
    """The three operations that put a screenshot in front of Moondream.

    Attributes:
        capture: The screenshot engine.
        client: The Moondream client.
        finder: Element location.
        ocr: Text reading.
        hypothalamus: The resource layer, consulted before any model loads.
        audit: Audit log, for a refused load.
        kill_switch: Re-checked on every call.
    """

    def __init__(
        self,
        capture: ScreenCapture,
        client: VisionClient,
        finder: Any,
        ocr: Any,
        hypothalamus: Optional[Any] = None,
        audit: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        """Wire the operations to their collaborators.

        Args:
            capture: The screenshot engine.
            client: The Moondream client.
            finder: The element finder.
            ocr: The OCR reader.
            hypothalamus: The Hypothalamus asked before the model loads.
            audit: Audit log, for a refused load.
            kill_switch: The switch re-checked on every call.
        """
        self.capture = capture
        self.client = client
        self.finder = finder
        self.ocr = ocr
        self.hypothalamus = hypothalamus
        self.audit = audit
        self.kill_switch = kill_switch

    def admit(self) -> Dict[str, Any]:
        """Ask the Hypothalamus whether the vision model may load.

        Returns:
            The budget verdict.

        Raises:
            VisionModelUnavailable: The model will not fit in the budget, or the
                resource layer is in cloud-only mode.
        """
        return vision_resources.admit(self.hypothalamus, vision_resources.MODEL, self.audit)

    async def describe(
        self,
        decision: Any,
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
            A record with the description as
            :class:`~brain.occipital_lobe.untrusted_text.UntrustedText`, its
            confidence, and the threshold verdict.
        """
        vision_guards.verify_decision(
            decision, vision_guards.target_for("describe", monitor, region), self.kill_switch
        )
        self.admit()
        capture = self.capture.capture(
            decision, monitor=monitor, region=region, label="describe", operation="describe"
        )
        reply = await self.client.ask("describe", [capture.image], question=question)
        source = vision_guards.target_for("describe", monitor, region)
        confidence = plausible_confidence(reply.text)
        verdict = thresholds.verdict(confidence, DESCRIBE_JOB)
        return {
            "description": reply.as_untrusted(source, confidence).to_dict(),
            "status": verdict["status"],
            "needs_confirmation": verdict["status"] != "accepted",
            "confidence": confidence,
            "threshold": verdict["threshold"],
            "job": verdict["job"],
            "capture": capture.summary(),
        }

    async def find_element(
        self,
        decision: Any,
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
        vision_guards.verify_decision(
            decision, vision_guards.target_for("find", monitor, region), self.kill_switch
        )
        self.admit()
        capture = self.capture.capture(
            decision, monitor=monitor, region=region, label="find", operation="find"
        )
        return await self.finder.locate(decision, capture, description, FIND_JOB)

    async def read_text(
        self,
        decision: Any,
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
        vision_guards.verify_decision(
            decision, vision_guards.target_for("ocr", monitor, region), self.kill_switch
        )
        self.admit()
        capture = self.capture.capture(
            decision, monitor=monitor, region=region, label="ocr", operation="ocr"
        )
        return await self.ocr.read(decision, capture, region, OCR_JOB)