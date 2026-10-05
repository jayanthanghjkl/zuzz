"""Occipital Lobe — screen capture engine.

Takes a screenshot, under permission. The module is thin on purpose: everything it
does is one decision and one hand-off, because the invariants it has to hold are
worth more than the convenience of having the whole pipeline in one place.

* **Permission is the signature.** :meth:`ScreenCapture.capture` takes a
  :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` as its
  first argument and refuses without one, so no caller can reach the framebuffer
  by accident and no refactor downstream can change that.
* **Regions are monitor-local.** A crop is ``(x, y, w, h)`` relative to the monitor
  it is taken from, so ``(0, 0, 320, 48)`` means the same thing on the primary
  display and on a monitor to its right. The returned
  :class:`~brain.occipital_lobe.capture.Capture` records where its top-left corner
  actually is in desktop pixels, so a coordinate read out of the image converts
  back to a click — see :attr:`~brain.occipital_lobe.capture.Capture.to_desktop`.
* **Crops are refused, never clamped.** A box that leaves the monitor raises. A
  clamped crop would be a picture of something other than what was asked for, and
  every coordinate derived from it would be wrong by an amount the caller cannot
  see.
* **Storage is somebody else's problem.** This module hands a capture to
  :class:`~brain.occipital_lobe.screenshot_store.Screenshots` and never opens a
  path itself, so there is exactly one place in the region where a screenshot can
  reach the disk.

Reading the layout is *not* gated. The monitor list is the same class of read as
the trust level — a gauge nobody would consult if it needed a permission — and the
caller needs it to build the target string for the capture it is about to ask
permission for.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.occipital_lobe import image_ops, vision_guards
from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.capture_backend import CaptureBackend
from brain.occipital_lobe.mss_backend import MssBackend
from brain.occipital_lobe.screenshot_store import SCREENSHOT_DIR, Screenshots


class ScreenCapture:
    """Takes screenshots under permission.

    Attributes:
        backend: The capture backend; ``mss`` unless one is injected.
        store: Where screenshots are written when asked to store one.
        kill_switch: Re-checked on every capture, so a stop that lands between the
            decision and the grab is caught rather than assumed away.
        logger: Activity logger; None means nothing is logged.
    """

    def __init__(
        self,
        backend: Optional[CaptureBackend] = None,
        store: Optional[Screenshots] = None,
        kill_switch: Optional[KillSwitch] = None,
        logger: Optional[Any] = None,
    ) -> None:
        """Wire the capture engine.

        Args:
            backend: The backend to capture through. Injectable so the whole
                region can be exercised with no display attached.
            store: The screenshot store; defaults to ``storage/screenshots/``.
            kill_switch: The switch to re-check, or the process-wide one.
            logger: Activity logger.
        """
        self.backend = backend if backend is not None else MssBackend()
        self.store = store if store is not None else Screenshots(SCREENSHOT_DIR, logger)
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.logger = logger

    def use_backend(self, backend: CaptureBackend) -> None:
        """Replace the capture backend.

        Args:
            backend: The backend to use from now on.

        Returns:
            Nothing. Exists so the composition root can install the real ``mss``
            backend while every test builds its own engine with a fake one, and so
            a caller with no display can still ask what the layout *would* be.
        """
        self.backend = backend

    def monitors(self) -> List[Dict[str, Any]]:
        """Return every attached monitor.

        Returns:
            One record per monitor with ``left``, ``top``, ``width``, ``height``,
            ``primary`` and ``index``. Index order is the capture index.
        """
        return [dict(monitor, index=index) for index, monitor in enumerate(self.backend.layout())]

    def monitor_at(self, x: int, y: int) -> Optional[Dict[str, Any]]:
        """Return the monitor containing a desktop point.

        Args:
            x: Horizontal desktop coordinate.
            y: Vertical desktop coordinate.

        Returns:
            The monitor record, or None when the point is on no attached display —
            which is a real answer, because a coordinate validated against one
            monitor's bounds is not a coordinate validated against the desktop.
        """
        for monitor in self.monitors():
            left, top = monitor["left"], monitor["top"]
            if (
                left <= x < left + monitor["width"]
                and top <= y < top + monitor["height"]
            ):
                return monitor
        return None

    def capture(
        self,
        decision: Any,
        monitor: int = 0,
        region: Optional[Sequence[int]] = None,
        label: str = "",
        operation: str = "capture",
    ) -> Capture:
        """Take one screenshot, under permission.

        Args:
            decision: An allowed ``visual_capture`` decision.
            monitor: Zero-based monitor index; negative counts from the end, as
                Python sequences do.
            region: ``(x, y, width, height)`` in monitor-local pixels, or None for
                the whole monitor.
            label: Short name for the log and the stored filename.
            operation: The operation the decision was issued for.

        Returns:
            The capture, with its image and its desktop origin.

        Raises:
            DecisionRefused: No decision, or one that does not permit this.
            KillSwitchRefused: The stop was engaged after the decision was issued.
            DryRunRefused: The decision describes the capture rather than granting it.
            DecisionReplayRefused: The decision was for another operation, monitor
                or region.
            MonitorNotFound: No such monitor is attached.
            CropOutOfBounds: The region leaves the monitor, has no area, or is
                larger than the single-read ceiling.
        """
        # ``operation`` rather than a hard-coded ``"capture"``, because the three
        # model-backed operations all need a screenshot and all hold a decision
        # issued for *their own* name. Verifying those against ``capture`` instead
        # refused every ``describe``, ``find`` and ``ocr`` at the last line —
        # "decision was for 'describe:monitor=0', not 'capture:monitor=0'" — which
        # left the Occipital Lobe's entire reason for existing unreachable. The
        # check is not weakened: the decision still has to name this exact
        # operation, monitor and region.
        target = vision_guards.target_for(operation, monitor, region)
        vision_guards.verify_decision(decision, target, self.kill_switch)
        layout = self.backend.layout()
        chosen = vision_guards.resolve_monitor(monitor, layout)
        box = vision_guards.resolve_region(
            region, (chosen["left"], chosen["top"], chosen["width"], chosen["height"])
        )
        image, rect = self.backend.grab(monitor)
        origin = (rect["left"], rect["top"])
        size = (rect["width"], rect["height"])
        if box is not None:
            image = image_ops.crop(image, box)
            origin = (origin[0] + box[0], origin[1] + box[1])
            size = (box[2], box[3])
        self._log("capture", monitor=monitor, region=box, size=size, label=label)
        return Capture(
            image=image,
            width=size[0],
            height=size[1],
            monitor=monitor,
            origin=origin,
            region=box,
            label=label,
        )

    def _log(self, action: str, **fields: Any) -> None:
        """Write one record to the activity log, if there is one.

        Args:
            action: Short verb phrase, e.g. ``"capture"``.
            **fields: Extra structured detail.

        Returns:
            Nothing. A logger that raises must not turn a successful screenshot
            into a failed call, so its errors are swallowed: the pixels have
            already been read and the caller is waiting for an answer about them.
        """
        if self.logger is None:
            return
        try:
            self.logger.log("occipital_lobe", action, **fields)
        except Exception:
            pass