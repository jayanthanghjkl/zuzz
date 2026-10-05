"""Occipital Lobe — vision refusals.

The region's errors, so the API can map each onto the right HTTP status instead
of collapsing every failure into a 500. Three distinctions matter to a caller:

* a **refusal** (something about the request or the machine) is the user's problem
  to fix — a bad crop, no permission, the model will not fit;
* :class:`VisionModelUnavailable` is transient and worth retrying later;
* :class:`ElementNotFound` and :class:`SensitiveScreenDetected` are answers, not
  faults. Neither is an error condition in the sense of "something broke": the
  first means the model did not find what was asked for, the second means ZUZZ
  looked and deliberately did the safe thing. The orchestrator turns both into
  structured results rather than exceptions at the API boundary, but they are
  raised internally so no caller can forget the distinction.

The hierarchy mirrors the Body layers: everything is a :class:`VisionRefused`,
anything ending ``KillSwitchRefused`` is a 409 (a stop outranks any judgement
about the action), and anything ending ``ProtectedRefused`` is a 403 (no trust
level would ever allow it).
"""

from __future__ import annotations


class VisionRefused(Exception):
    """Base class for every Occipital Lobe refusal."""


# -- the stop, and the decision ---------------------------------------------


class KillSwitchRefused(VisionRefused):
    """The kill switch was engaged, or engaged after the decision was issued."""


class DecisionRefused(VisionRefused):
    """A vision operation reached the Occipital Lobe without permission.

    Covers a missing decision, one that does not allow execution, and one issued
    for a different operation or target.
    """


class DecisionReplayRefused(DecisionRefused):
    """A decision was presented for a different operation or target."""


class DryRunRefused(VisionRefused):
    """A dry-run decision reached the Occipital Lobe.

    A dry run describes the capture; it must not take one. A screenshot is a read
    of the user's whole desktop, so honouring it would mean a preview button
    quietly photographed the screen.
    """


# -- targets the Occipital Lobe will not accept ------------------------------


class ProtectedRefused(VisionRefused):
    """The request is something ZUZZ must not do at any trust level."""


class CropOutOfBounds(ProtectedRefused):
    """The requested region falls outside the monitor it was measured against.

    A refusal rather than a clamp. Clamping would hand the caller an image of
    something other than what they asked for — a wide crop silently narrowed to
    the top-left corner — and every coordinate derived from it would then be
    wrong by an amount the caller cannot see.
    """


class MonitorNotFound(ProtectedRefused):
    """No such monitor is attached, or the layout could not be read."""


class SensitiveScreenDetected(ProtectedRefused):
    """The captured screen carries credential material.

    Raised by storage, not by capture: the pixels are in memory either way, and
    the refusal is about writing them to disk where they would outlive the task
    and be read by anything with a filesystem. The caller may pass an explicit
    acknowledgement to store anyway, which is recorded in the audit log.
    """


# -- the model and the answer ------------------------------------------------


class VisionModelUnavailable(VisionRefused):
    """Moondream could not be reached, or the Hypothalamus refused to load it.

    Carries the reason so the caller can tell "Ollama is not running" from "there
    is no VRAM for it", which need different answers from a human: the first is
    fixed by starting a service, the second by freeing memory or accepting a
    cloud route.
    """

    def __init__(self, message: str, reason: str = "") -> None:
        """Record the message and the machine-readable reason.

        Args:
            message: Human-readable explanation.
            reason: Short machine-readable tag, e.g. ``vram_budget`` or
                ``transport``.
        """
        super().__init__(message)
        self.reason = reason or "unavailable"


class ElementNotFound(VisionRefused):
    """The model returned no usable box for the requested element.

    Attributes:
        confidence: The confidence the finder reached before giving up, which is
            what tells a caller whether the element was absent or merely doubtful.
    """

    def __init__(self, message: str, confidence: float = 0.0) -> None:
        """Record the message and the confidence reached.

        Args:
            message: Human-readable explanation.
            confidence: Best confidence seen, 0.0 when nothing was parsed.
        """
        super().__init__(message)
        self.confidence = confidence


class VisionTimeout(VisionRefused):
    """The vision model did not answer within the timeout."""


class OcrUnavailable(VisionRefused):
    """No text-reading engine could be used.

    Distinct from :class:`VisionModelUnavailable` because the fallback ladder may
    still have one engine left; this is the answer when neither is available.
    """