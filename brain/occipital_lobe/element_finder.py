"""Occipital Lobe — UI element finder.

Turns "find the Search bar" into coordinates. The pipeline is deliberately boring:

    capture -> ask Moondream -> parse a box -> scale to the frame -> score it ->
    compare against the job's threshold -> return a record, or say it was not found

Nothing here decides whether to *click*. It reports, and
:mod:`brain.occipital_lobe.thresholds` decides. That split is the reason the region
is safe to drive: a caller that wants a coordinate asks for one and gets the
confidence attached, and the question of whether that confidence is good enough
belongs to the threshold config and to the API's confirmation flow — not to a
heuristic buried in a click handler.

The confidence itself is derived in :mod:`brain.occipital_lobe.element_scoring`, and
the coordinate spaces — pixel, normalised and desktop — are assembled there too, so
the arithmetic behind every click this region enables can be read in one place.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.occipital_lobe import element_scoring, thresholds, vision_guards
from brain.occipital_lobe.box_parser import parse_box
from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.exceptions import ElementNotFound
from brain.occipital_lobe.vision_client import VisionClient

#: The job this finder answers for. Element locations get used to click or to fill a
#: form, which is the strictest of the four thresholds in the config: a wrong box acts
#: on the wrong target, while a wrong description is merely a wrong sentence.
JOB = "click_or_form_fill"


class ElementFinder:
    """Locates UI elements from a natural-language description.

    Attributes:
        client: The vision client the question is asked through.
        kill_switch: Re-checked on every call.
        logger: Activity logger; None means nothing is logged.
    """

    def __init__(
        self,
        client: VisionClient,
        kill_switch: Optional[KillSwitch] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the finder.

        Args:
            client: The vision client to ask.
            kill_switch: The switch to re-check, or the process-wide one.
            logger: Activity logger.
        """
        self.client = client
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.logger = logger

    async def locate(
        self,
        decision: Any,
        capture: Capture,
        description: str,
        job: str = JOB,
    ) -> Dict[str, Any]:
        """Locate one element in one capture.

        Args:
            decision: An allowed ``visual_capture`` decision for a ``find``.
            capture: The screenshot to look in.
            description: What to look for, in the user's words.
            job: Which threshold to judge the answer against.

        Returns:
            A record carrying ``status`` (``found``, ``needs_confirmation`` or
            ``not_found``), the derived ``confidence``, the ``threshold``, the ``box``
            in image pixels, the ``center`` in image and desktop coordinates, and the
            ``normalized`` box as fractions of the frame.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            DecisionReplayRefused: The decision was for another operation or target.
            VisionModelUnavailable: The model could not be reached.
            ElementNotFound: The model returned no usable box.
        """
        target = vision_guards.target_for("find", capture.monitor, capture.region)
        vision_guards.verify_decision(decision, target, self.kill_switch)
        reply = await self.client.ask("find", [capture.image], question=description)
        parsed = parse_box(reply.text, capture.width, capture.height)
        if not parsed.found:
            raise ElementNotFound(
                f"no box for {description!r}: {parsed.reason}", confidence=0.0
            )
        confidence = element_scoring.score(parsed.confidence, description, reply.text)
        verdict = thresholds.verdict(confidence, job)
        return element_scoring.record(
            capture, parsed.box, confidence, verdict, description, reply.text
        )