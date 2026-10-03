"""Body/Hands — the guards every operation passes through.

Every Hands method runs the same checks before it puts anything on the machine,
in the same order, so no operation can forget one:

1. **The decision.** Is there a real
   :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`? Does it
   allow this operation? Was it issued for *this* target, or replayed from
   another one? Is the kill switch still disengaged? A decision issued before an
   emergency stop must not survive the stop.
2. **The text.** Text that is a command in one window is a refusal in all of
   them, because ZUZZ cannot see which window has focus.

The gate makes the same decisions, so this is defence in depth: the gate is the
boundary, the Body is the last line.

Ordering matters and is deliberate. The switch is consulted before the
decision's own verdict, so a stop is reported as a stop rather than as an action
that merely happened to need confirming.

The remaining guards live beside the thing they guard, because that is where
their inputs come from: :mod:`body.hands.hotkey_policy` for key combinations,
:mod:`body.hands.window_guards` for windows and processes, and
:meth:`body.hands.screens.Screens.check` for coordinates.
"""

from __future__ import annotations

from typing import Any, Optional

from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.spinal_cord.kill_switch import KillSwitch, get_kill_switch

from body.hands import app_registry
from body.hands.exceptions import (
    DangerousTextRefused,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    KillSwitchRefused,
)
from body.hands.text_scanner import scan_bulk_email, scan_text


def verify_decision(
    decision: Any,
    category: str,
    target: str,
    kill_switch: Optional[KillSwitch] = None,
) -> None:
    """Refuse the call unless the decision permits it.

    Args:
        decision: What the caller was handed.
        category: The action category this method performs.
        target: The resolved target the method was asked to act on.
        kill_switch: The switch to re-check, or the process-wide one when None.

    Raises:
        DecisionRefused: No decision, a decision that does not allow execution,
            or one made while the switch was engaged.
        DecisionReplayRefused: The decision names another operation or target.
        DryRunRefused: The decision is a dry run.
        KillSwitchRefused: The switch was engaged after the decision was issued.
    """
    if not isinstance(decision, PermissionDecision):
        raise DecisionRefused(
            "refusing to actuate without a PermissionDecision from "
            "the permission gate"
        )
    if decision.dry_run:
        raise DryRunRefused(
            "dry-run decisions describe the action; they never reach the desktop"
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

    Unlike the Fingers version this is not path arithmetic, so it does not try
    to be clever. Desktop targets are names, titles and coordinates: a decision
    for ``chrome`` covers ``chrome.exe`` because both resolve to the same
    application, and it does **not** cover ``firefox`` because two spellings of
    one application are not two applications. An empty granted target matches
    everything, so a caller who passed no target is still checked by category.

    Args:
        granted: The target the decision was issued for.
        acted_on: The resolved target the operation will touch.

    Returns:
        True when the decision covers this target.
    """
    left = str(granted or "").strip().lower()
    right = str(acted_on or "").strip().lower()
    if not left or left == right:
        return True
    try:
        return app_registry.resolve_app(left)["executable"].lower() == right
    except Exception:
        return False


def guard_text(text: str) -> None:
    """Refuse text that carries a pattern ZUZZ must not type.

    Typing has no target. ``rm -rf /`` is ordinary text in an editor and
    catastrophic in a terminal, and ZUZZ cannot see which window has focus — so
    the text itself is what gets judged, on every typed string, every time.

    Args:
        text: The string about to be typed.

    Raises:
        DangerousTextRefused: The text matches a dangerous pattern.
    """
    findings = scan_text(text)
    if not findings:
        return
    labels = ", ".join(finding["label"] for finding in findings)
    raise DangerousTextRefused(
        f"refusing to type text matching {labels}. ZUZZ cannot see which window "
        f"has focus, so text that is a command in one window is a refusal here."
    )


def bulk_email_finding(text: str, threshold: int = 5) -> Optional[dict]:
    """Return a finding when text carries a mailing list.

    Mailing many people is an action a person should take deliberately, so this
    is escalated rather than refused: the caller turns the finding into a higher
    trust category and the gate asks. A false positive costs one confirmation.

    Args:
        text: The string about to be typed.
        threshold: How many distinct addresses count as bulk.

    Returns:
        The finding dict, or None below the threshold.
    """
    return scan_bulk_email(text, threshold)