"""Parietal Lobe — the guards every system read passes through.

Every method in the region runs the same checks before it asks the OS anything, in
the same order, so no method can forget one. **The decision**: is there a real
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`, does it
allow a ``system_awareness`` action, was it issued for *this* operation, and is the
kill switch still disengaged — a decision issued before an emergency stop must not
survive the stop. **The subject**: is the requested monitor attached.

The gate makes the same decisions, so this is defence in depth: the gate is the
boundary, the Parietal Lobe is the last line. It lives here rather than in each of the
seven modules so that "did you check?" has one answer to grep for. Ordering is
deliberate — the switch is consulted before the decision's own verdict, so a stop is
reported as a stop rather than as a read that merely needed confirming.

Two things this module deliberately does **not** do, because both are the failure this
region is most exposed to:

* **It never widens the category.** The Amygdala's ``system_path`` pattern would match
  an executable path, and system awareness reads window titles and process names, not
  paths. There is nothing here to exempt because nothing here is scored as a file
  operation — the category and the target are the whole of what the gate sees.
* **It never puts a window title in the target.** :func:`target_for` builds
  ``"active-window"``, not ``"active-window:Invoices — 2026 Q3.xlsx"``. The target goes
  into the Amygdala's regex scoring, into the audit log, and into every confirmation
  popup, and a document title is the user's data. It travels in ``Action.detail``
  instead, which is where :meth:`~brain.prefrontal_cortex.action.Action.describe` and
  the log can carry it without the safety layer reading it as a path.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from brain.prefrontal_cortex.action import ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from brain.parietal_lobe.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
    NoSuchMonitor,
)

#: The category every system read is gated as.
SYSTEM_AWARENESS = str(ActionCategory.SYSTEM_AWARENESS)

#: Every operation the region performs, in the order a user asks for them. Kept as a
#: tuple rather than scattered through the call sites so that a new operation cannot
#: be added to the orchestrator without also being a decision the guard will accept.
OPERATIONS = (
    "active-window",
    "screen-info",
    "running-apps",
    "mouse-position",
    "detect-context",
    "snapshot",
)


def verify_decision(
    decision: Any,
    target: str,
    kill_switch: Optional[KillSwitch] = None,
    category: str = SYSTEM_AWARENESS,
) -> None:
    """Refuse the call unless the decision permits it.

    Args:
        decision: What the caller was handed.
        target: The canonical target for the operation about to run — see
            :func:`target_for`.
        kill_switch: The switch to re-check, or the process-wide one when None.
        category: The action category this method performs.

    Raises:
        DecisionRefused: No decision, one that does not allow execution, or one made
            while the switch was engaged.
        DecisionReplayRefused: The decision names another operation.
        DryRunRefused: The decision is a dry run.
        KillSwitchRefused: The switch was engaged after the decision was issued.
    """
    if not isinstance(decision, PermissionDecision):
        raise DecisionRefused(
            "refusing to read the system's state without a PermissionDecision "
            "from the permission gate"
        )
    if decision.dry_run:
        raise DryRunRefused(
            "dry-run decisions describe the read; they never perform it"
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
    """Return True when a decision covers the operation about to be run.

    Exact matching, no prefix arithmetic: approving ``running-apps`` does not approve
    ``snapshot``, because a snapshot runs all six operations and is the one most worth
    naming in an audit entry. An empty granted target matches everything, so a caller
    who passed no target is still checked by category.

    Args:
        granted: The target the decision was issued for.
        acted_on: The target the operation will act on.

    Returns:
        True when the decision covers this operation.
    """
    left = str(granted or "").strip().lower()
    right = str(acted_on or "").strip().lower()
    return not left or left == right


def target_for(operation: str) -> str:
    """Return the canonical target string for one system read.

    The target is **the operation name and nothing else**. A monitor index was in
    here during development and is the exact drift this project has already been bitten
    by once: ``propose(operation, monitor)`` built ``"screen-info:monitor=0"`` while
    :meth:`~brain.parietal_lobe.parietal_lobe.ParietalLobe._verify` checked
    ``"screen-info"``, so every approved read was refused at the last line with a
    replay error and the whole subsystem looked unreachable. Removing the parameter
    removes the possibility rather than fixing one instance of it.

    It is also the more honest spelling. ``get_screen_info`` returns the *whole* layout,
    so it does not act on monitor 1 — a target naming a monitor would claim a
    narrowness the read does not have, and would imply an approval for "tell me about
    monitor 1" is different from one for "tell me about the screens". The monitor a
    caller is interested in is reported in the response and recorded in
    ``Action.detail``, where it belongs.

    Args:
        operation: One of :data:`OPERATIONS`.

    Returns:
        The target, e.g. ``"active-window"``.

    Raises:
        ValueError: For an operation this region does not perform. Raising here is
            deliberate: a typo in an operation name would otherwise produce a target
            that no decision could ever match, and the failure would surface as a
            replay error on a subsystem that is wired correctly.
    """
    name = str(operation).strip().lower()
    if name not in OPERATIONS:
        raise ValueError(
            f"unknown system-awareness operation: {operation!r}. "
            f"One of {', '.join(OPERATIONS)}."
        )
    return name


def resolve_monitor(index: int, monitors: Sequence[dict]) -> dict:
    """Return the monitor record at ``index``.

    Args:
        index: Zero-based monitor index; negative values count from the end, as Python
            sequences do, so ``-1`` is the last attached display.
        monitors: The layout, as
            :meth:`brain.parietal_lobe.screen_info.ScreenInfo.monitors` returns it.

    Returns:
        The monitor record, carrying ``index``.

    Raises:
        NoSuchMonitor: No monitor is attached, or the index is out of range.
    """
    if not monitors:
        raise NoSuchMonitor("no monitors reported; the screen layout could not be read")
    try:
        return dict(monitors[index], index=index)
    except (IndexError, TypeError) as exc:
        raise NoSuchMonitor(
            f"monitor {index} is not attached. {len(monitors)} monitor(s) available."
        ) from exc


def monitor_at(monitors: Sequence[dict], x: int, y: int) -> Optional[dict]:
    """Return the monitor containing a desktop point.

    Args:
        monitors: The layout.
        x: Horizontal desktop coordinate.
        y: Vertical desktop coordinate.

    Returns:
        The monitor record with its index, or None when the point is on no attached
        display. None is a real answer rather than a failure: a coordinate can be off
        the desktop during a resolution change, and reporting "monitor 0" for it would
        place a click somewhere the user never pointed at.
    """
    for index, monitor in enumerate(monitors):
        left, top = monitor.get("left", 0), monitor.get("top", 0)
        width, height = monitor.get("width", 0), monitor.get("height", 0)
        if left <= x < left + width and top <= y < top + height:
            return dict(monitor, index=index)
    return None


def available(names: Sequence[str]) -> List[str]:
    """Return which of ``names`` are importable on this machine.

    Used to report honestly at the composition root rather than to gate a call: a
    missing ``pygetwindow`` should degrade the region to what it can still read and
    say so in ``status()``, not take the whole agent down.

    Args:
        names: Module names to test.

    Returns:
        The subset that is present, in the order given.
    """
    import importlib.util

    found: List[str] = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is not None:
                found.append(name)
        except (ImportError, ValueError):
            continue
    return found


def missing(names: Sequence[str]) -> List[str]:
    """Return which of ``names`` are **not** importable.

    The complement of :func:`available`, because the useful half of the answer for a
    status panel is what is broken, and a caller holding the full list has to compute
    the difference again.

    Args:
        names: Module names to test.

    Returns:
        The subset that is absent, in the order given.
    """
    present = set(available(names))
    return [name for name in names if name not in present]
