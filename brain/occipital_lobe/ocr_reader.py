"""Occipital Lobe — reading text off a screen.

Two engines and a ladder between them, following the approved Q7 decision: Moondream
first, Tesseract as the second read, consulted only when the first answer is not
trustworthy. The common case therefore stays one model call, and the second one is
paid for exactly when it might change the answer. The engines and the confidence
rules are in :mod:`brain.occipital_lobe.ocr_engines`; this module is the part that has
to be gated.

What the reader guarantees, whatever the engine:

* **The text is marked untrusted.** It comes back as
  :class:`~brain.occipital_lobe.untrusted_text.UntrustedText`, never as a bare
  ``str``. A screen is somebody else's words, and the moment they are a plain string
  the first f-string that interpolates them has handed an attacker the reasoning
  stream.
* **Confidence is compared against the OCR threshold**, not against a constant.
  ``ocr_text_read`` has its own number in ``config/vision_thresholds.json``, because
  OCR output is transcribed onward: a misread digit in an amount becomes a wrong
  transfer rather than a wrong sentence.
* **Disagreement is surfaced, not averaged.** When the two engines read the same
  pixels differently the higher-confidence answer wins and the fact is reported, so
  the user is told to check it rather than handed a silent choice.
* **A region is never silently enlarged.** The requested box is validated against the
  capture before anything is cropped, so a request for a 320x48 field cannot turn
  into a whole-screen transcript.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.occipital_lobe import image_ops, thresholds, vision_guards
from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.exceptions import VisionModelUnavailable
from brain.occipital_lobe.ocr_engines import agree, plausible_confidence, tesseract
from brain.occipital_lobe.untrusted_text import UntrustedText
from brain.occipital_lobe.vision_client import VisionClient

#: The threshold job a text read is judged against.
JOB = "ocr_text_read"


class OcrReader:
    """Reads text from a captured region.

    Attributes:
        client: The vision client Moondream is asked through.
        kill_switch: Re-checked on every call.
        logger: Activity logger; None means nothing is logged.
    """

    def __init__(
        self,
        client: VisionClient,
        kill_switch: Optional[KillSwitch] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the reader.

        Args:
            client: The vision client to ask.
            kill_switch: The switch to re-check, or the process-wide one.
            logger: Activity logger.
        """
        self.client = client
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.logger = logger

    async def read(
        self,
        decision: Any,
        capture: Capture,
        region: Optional[Sequence[int]] = None,
        job: str = JOB,
    ) -> Dict[str, Any]:
        """Read the text in a region of a capture.

        Args:
            decision: An allowed ``visual_capture`` decision for an ``ocr``.
            capture: The screenshot to read.
            region: ``(x, y, width, height)`` in **image** pixels, or None for the
                whole capture.
            job: Which threshold to judge the read against.

        Returns:
            A record carrying the text as
            :class:`~brain.occipital_lobe.untrusted_text.UntrustedText`, the engine
            that produced it, the confidence, whether the second engine agreed, and
            the threshold verdict.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            DecisionReplayRefused: The decision was for another operation or target.
            CropOutOfBounds: The region leaves the capture.
            VisionModelUnavailable: Moondream could not be reached and no fallback
                engine was available.
        """
        target = vision_guards.target_for("ocr", capture.monitor, region)
        vision_guards.verify_decision(decision, target, self.kill_switch)
        box = vision_guards.resolve_region(region, (0, 0, capture.width, capture.height))
        image = capture.image if box is None else image_ops.crop(capture.image, box)
        ladder = thresholds.fallback_policy()
        try:
            reply = await self.client.ask("ocr", [image])
        except VisionModelUnavailable:
            second = tesseract(image)
            if second is None:
                raise
            return self._record(second, second, ladder, job, region, None)
        confidence = plausible_confidence(reply.text)
        if confidence >= _trigger(ladder):
            return self._record(
                UntrustedText(reply.text, target, confidence, "ocr"),
                "moondream",
                ladder,
                job,
                region,
                None,
            )
        second = tesseract(image)
        if second is None:
            # Nothing to compare against. The Moondream read stands on its own, at
            # whatever confidence it earned — reporting it as agreed would be a lie.
            return self._record(
                UntrustedText(reply.text, target, confidence, "ocr"),
                "moondream",
                ladder,
                job,
                region,
                None,
            )
        agreed = agree(reply.text, second.text)
        if agreed:
            return self._record(second, "moondream+tesseract", ladder, job, region, True)
        return self._record(second, "tesseract", ladder, job, region, False)

    def _record(
        self,
        text: UntrustedText,
        engine: str,
        ladder: Dict[str, Any],
        job: str,
        region: Optional[Sequence[int]],
        agreed: Optional[bool],
    ) -> Dict[str, Any]:
        """Assemble the record a caller transcribes from.

        Args:
            text: The read, marked untrusted.
            engine: Which engine produced it.
            ladder: The configured fallback policy, echoed so the UI can explain the
                ladder rather than guessing at it.
            job: The threshold job.
            region: The region read, for the log.
            agreed: Whether the two engines agreed, or None when only one ran.

        Returns:
            The record.
        """
        score = thresholds.verdict(text.confidence, job)
        return {
            "text": text.to_dict(),
            "engine": engine,
            "agreed": agreed,
            "flagged": agreed is False,
            "status": score["status"],
            "needs_confirmation": score["status"] != "accepted",
            "confidence": text.confidence,
            "threshold": score["threshold"],
            "job": score["job"],
            "region": list(region) if region else None,
            "on_disagreement": ladder.get("on_disagreement", ""),
            "on_total_failure": ladder.get("on_total_failure", ""),
        }


def _trigger(ladder: Dict[str, Any]) -> float:
    """Return the confidence at or above which the second engine is skipped.

    Args:
        ladder: The configured fallback policy.

    Returns:
        The trigger, so a read at or above it costs one model call.
    """
    return float(ladder.get("secondary_trigger_below", 0.85))