"""Occipital Lobe — the guards every vision operation passes through.

Every capture and every model call runs the same checks before it looks at the
screen, in the same order, so no method can forget one. **The decision**: is there a
real :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`, does it
allow a ``visual_capture`` action, was it issued for *this* operation and target, and
is the kill switch still disengaged — a decision issued before an emergency stop must
not survive the stop. **The region**: is the requested crop inside the monitor it is
measured against, since a crop of the wrong pixels produces coordinates wrong by an
amount the caller cannot see, so that is a refusal rather than a clamp.

The gate makes the same decisions, so this is defence in depth: the gate is the
boundary, the Occipital Lobe is the last line. It lives here rather than in each of
the six modules so that "did you check?" has one answer to grep for. Ordering is
deliberate — the switch is consulted before the decision's own verdict, so a stop is
reported as a stop rather than as an operation that merely needed confirming.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from brain.prefrontal_cortex.action import ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.occipital_lobe.exceptions import (
    CropOutOfBounds,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
    MonitorNotFound,
)

#: The category every screen capture and vision analysis is gated as.
VISUAL_CAPTURE = str(ActionCategory.VISUAL_CAPTURE)

#: Hard ceiling on a single OCR region, in pixels. Matches the Phase 3 plan:
#: feeding Moondream the whole screen to "read a region" is both slow and a way to
#: obtain a whole-screen transcript under the banner of a small request.
MAX_REGION_PIXELS = 1920 * 1080


def verify_decision(
    decision: Any,
    target: str,
    kill_switch: Optional[KillSwitch] = None,
    category: str = VISUAL_CAPTURE,
) -> None:
    """Refuse the call unless the decision permits it.

    Args:
        decision: What the caller was handed.
        target: The resolved target the operation will act on — see
            :func:`target_for` for the spelling.
        kill_switch: The switch to re-check, or the process-wide one when None.
        category: The action category this method performs.

    Raises:
        DecisionRefused: No decision, one that does not allow execution, or one
            made while the switch was engaged.
        DecisionReplayRefused: The decision names another operation or target.
        DryRunRefused: The decision is a dry run.
        KillSwitchRefused: The switch was engaged after the decision was issued.
    """
    if not isinstance(decision, PermissionDecision):
        raise DecisionRefused(
            "refusing to look at the screen without a PermissionDecision from "
            "the permission gate"
        )
    if decision.dry_run:
        raise DryRunRefused(
            "dry-run decisions describe the capture; they never take one"
        )
    switch = kill_switch if kill_switch is not None else get_kill_switch()
    if switch.is_killed():
        raise KillSwitchRefused(
            "kill switch engaged since this decision was issued"
        )
    if decision.blocked or not decision.allowed:
        raise DecisionRefused(
            f"the permission decision is {decision.decision}: {decision.reason}"
        )
    if str(decision.action.category) != str(category):
        raise DecisionReplayRefused(
            f"decision was for {decision.action.category}, not {category}"
        )
    if not same_target(decision.action.target, target):
        raise DecisionReplayRefused(
            f"decision was for {decision.action.target!r}, not {target!r}"
        )


def same_target(granted: str, acted_on: str) -> bool:
    """Return True when a decision covers the target about to be acted on.

    Targets are operation names rather than paths, so this is exact matching and
    no path arithmetic: a decision for ``describe`` covers ``describe`` and does
    not cover ``ocr``, because approving a description is not approving a
    transcription. An empty granted target matches everything, so a caller who
    passed no target is still checked by category.

    Args:
        granted: The target the decision was issued for.
        acted_on: The target the operation will act on.

    Returns:
        True when the decision covers this target.
    """
    left = str(granted or "").strip().lower()
    right = str(acted_on or "").strip().lower()
    return not left or left == right


def target_for(operation: str, monitor: int = 0, region: Optional[Sequence[int]] = None) -> str:
    """Return the canonical target string for one vision operation.

    Built the same way by the gate and by the region, so the two always agree on
    spelling. A decision is therefore refused when it was issued for a different
    monitor or a different box, which stops an approval for "look at monitor 0"
    being spent on "photograph the whole desktop".

    Args:
        operation: One of ``capture``, ``describe``, ``find``, ``ocr``, ``verify``.
        monitor: Monitor index.
        region: ``(x, y, width, height)`` in monitor-local pixels, or None.

    Returns:
        A target such as ``"capture:monitor=0"`` or ``"ocr:monitor=1;0,0,320,48"``.
    """
    text = f"{operation}:monitor={int(monitor)}"
    if region is not None:
        text += ";" + ",".join(str(int(value)) for value in region)
    return text


def screenshot_target_for(name: str) -> str:
    """Return the target string for reading one stored screenshot.

    Distinct from :func:`target_for` on purpose. A stored screenshot *is* a frame of
    the user's desktop, so fetching it is a ``visual_capture`` read and is gated — but
    it must not share a target string with a live capture, or an approval to "look at
    monitor 0" would be enough to hand over a stored frame and vice versa. The
    rejection in :func:`same_target` is exact matching, so the two spellings have to
    differ for the separation to hold.

    Args:
        name: The file name as returned by the store.

    Returns:
        A target such as ``"read-screenshot:shot_20261004_143012.png"``.
    """
    return f"read-screenshot:{Path(str(name)).name}"


def resolve_region(region: Optional[Sequence[int]], bounds: Tuple[int, int, int, int]) -> Optional[Tuple[int, int, int, int]]:
    """Validate a crop against the rectangle it will be taken from.

    Args:
        region: ``(x, y, width, height)`` in monitor-local pixels, or None for the
            whole monitor.
        bounds: ``(left, top, width, height)`` of the monitor. Monitor-local
            coordinates are compared against the monitor's own size rather than the
            desktop's, so a box means the same thing on a secondary display.

    Returns:
        The crop as a plain tuple, or None when no crop was requested.

    Raises:
        CropOutOfBounds: The box leaves the monitor, has a non-positive side, or
            exceeds :data:`MAX_REGION_PIXELS`.
    """
    if region is None:
        return None
    if len(tuple(region)) != 4:
        raise CropOutOfBounds(f"a region must be (x, y, width, height); got {tuple(region)!r}")
    x, y, width, height = (int(value) for value in region)
    _, _, monitor_width, monitor_height = bounds
    if width <= 0 or height <= 0:
        raise CropOutOfBounds(f"region {width}x{height} has no area; a zero-width crop is not a crop")
    if x < 0 or y < 0 or x + width > monitor_width or y + height > monitor_height:
        raise CropOutOfBounds(
            f"region ({x}, {y}, {width}, {height}) leaves the monitor, which is "
            f"{monitor_width}x{monitor_height}. Refused rather than clamped: a "
            "clamped crop is a picture of something other than what was asked for."
        )
    if width * height > MAX_REGION_PIXELS:
        raise CropOutOfBounds(
            f"region {width}x{height} is {width * height:,} pixels, above the "
            f"{MAX_REGION_PIXELS:,} ceiling for a single read"
        )
    return (x, y, width, height)


def resolve_monitor(index: int, monitors: Sequence[dict]) -> dict:
    """Return the monitor record at ``index``.

    Args:
        index: Zero-based monitor index; negative values count from the end, as
            Python sequences do, so ``-1`` is the last attached display.
        monitors: The layout, as :meth:`ScreenCapture.monitors` returns it.

    Returns:
        The monitor record, carrying ``index``.

    Raises:
        MonitorNotFound: No monitor is attached, or the index is out of range.
    """
    if not monitors:
        raise MonitorNotFound("no monitors reported; the screen layout could not be read")
    try:
        return dict(monitors[index], index=index)
    except (IndexError, TypeError) as exc:
        raise MonitorNotFound(
            f"monitor {index} is not attached. {len(monitors)} monitor(s) available."
        ) from exc