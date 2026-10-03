"""Face API — kill switch routes.

The emergency stop's user interface: status, trigger and reset. Triggering is
one POST and needs no confirmation — the whole point of a stop is that it works
while something is going wrong. Resetting is the opposite in every respect: it
requires typing a word, waits out a progressive cooldown, and is the only path
that re-arms the machine.

These routes never accept a reset from an agent. The confirmation evidence is
supplied by the UI, and :meth:`~brain.spinal_cord.kill_switch.KillSwitch.reset`
refuses without it.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.spinal_cord.kill_switch import ResetRefused
from brain.spinal_cord.stop_scope import StopScope
from brain.spinal_cord.watchdog import Watchdog

router = APIRouter()


class StopRequest(BaseModel):
    """A request to engage the emergency stop."""

    reason: str = Field("", description="What went wrong, shown in the UI")
    source: str = Field("ui", description="hotkey, watchdog, ui or process")
    last_action: str = Field("", description="The action in flight when it stopped")


class ResetRequest(BaseModel):
    """A request to disengage the emergency stop, with its confirmation."""

    typed_word: str = Field("", description=f"must be RESET")
    confirmed: bool = Field(False, description="the user clicked confirm")


def _scope(request: Request) -> StopScope:
    """Return the stop scope wired into the app."""
    return request.app.state.stop_scope


def _switch(request: Request):
    """Return the kill switch wired into the app.

    Taken from app state rather than the singleton so tests can substitute an
    isolated switch, and so the app always speaks about the same object the
    scope and the gate use.
    """
    return request.app.state.kill_switch


@router.get("/api/kill-switch")
async def kill_switch_status(request: Request) -> Dict[str, Any]:
    """Return whether ZUZZ is stopped, why, and whether a reset is possible.

    Args:
        request: Used to reach the kill switch and the stop scope.

    Returns:
        The switch status plus the last stop report.
    """
    return {
        **_switch(request).status(),
        "last_stop": _scope(request).last_report,
        "scheduler_paused": request.app.state.scheduler.paused,
    }


@router.post("/api/kill-switch/stop")
async def kill_switch_stop(request: Request, body: StopRequest) -> Dict[str, Any]:
    """Engage the emergency stop and carry it out.

    Stops scheduled jobs, terminates registered background processes and flags
    the cloud agents. No confirmation is required.

    Args:
        request: Used to reach the stop scope.
        body: Why and from where the stop was requested.

    Returns:
        The stop report, including anything that could not be stopped.

    Raises:
        HTTPException: 400 when the source is not a recognised trigger.
    """
    try:
        report = _scope(request).engage(reason=body.reason, source=body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.logger.log(
        "spinal_cord",
        "stop",
        source=body.source,
        reason=body.reason,
        last_action=body.last_action,
        paused=report["paused"],
        terminated=report["terminated"],
        cloud_flagged=report["cloud_flagged"],
    )
    return report


@router.post("/api/kill-switch/reset")
async def kill_switch_reset(request: Request, body: ResetRequest) -> Dict[str, Any]:
    """Disengage the emergency stop once the confirmation is satisfied.

    Args:
        request: Used to reach the kill switch, the scope and the scheduler.
        body: The confirmation evidence supplied by the user.

    Returns:
        The status after resetting.

    Raises:
        HTTPException: 409 when the confirmation or cooldown is not satisfied.
    """
    try:
        status = _switch(request).reset(typed_word=body.typed_word, confirmed=body.confirmed)
    except ResetRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request.app.state.scheduler.resume()
    request.app.state.logger.log("spinal_cord", "reset", source="ui")
    return status


@router.get("/api/kill-switch/watchdog")
async def watchdog_status(request: Request) -> Dict[str, Any]:
    """Run one watchdog poll and report what it saw.

    Exposed so the dashboard can show whether the watchdog is healthy and what it
    currently considers normal. A trip here stops ZUZZ.

    Args:
        request: Used to reach the watchdog.

    Returns:
        The poll report.
    """
    return request.app.state.watchdog.poll()


@router.post("/api/kill-switch/watchdog")
async def configure_watchdog(request: Request, thresholds: Dict[str, Any]) -> Dict[str, Any]:
    """Adjust the watchdog's thresholds.

    **Tightening a threshold is always allowed; loosening one needs permission
    that no trust level grants.** Every limit here is an upper bound the watchdog
    trips at — ``deletions >= deletion_limit`` — so a larger number means the
    signal fires later or never. That is disarming the only thing watching for
    runaway behaviour, and it used to be a single unauthenticated POST away.

    The asymmetry is deliberate. Making the watchdog stricter cannot reduce
    safety, so it needs no permission and needs no dialog. Making it laxer is
    ``disable_safety``, which the trust matrix blocks at every level including
    five: there is no trust level at which "turn the watchdog off" is allowed.

    Only three of the seven limits are tunable. The click, keystroke, app-open and
    blocked-hotkey limits are fixed in code, so they cannot be reached from here
    at all.

    Args:
        request: Used to reach the watchdog and the shared gate.
        thresholds: Any of ``cpu_percent``, ``cpu_streak_limit``, ``deletion_limit``.

    Returns:
        The watchdog's current configuration.

    Raises:
        HTTPException: 400 when a threshold is not usable or would weaken a
            signal, 403 when weakening was refused.
    """
    from brain.prefrontal_cortex.action import Action, ActionCategory
    from brain.prefrontal_cortex.permission_gate import PermissionGate

    watchdog: Watchdog = request.app.state.watchdog
    gate = PermissionGate(kill_switch=request.app.state.kill_switch)
    level = request.app.state.trust.current()

    for key in ("cpu_percent", "cpu_streak_limit", "deletion_limit"):
        if key not in thresholds:
            continue
        value = float(thresholds[key])
        if value < 0:
            raise HTTPException(status_code=400, detail=f"{key} must not be negative")
        # A limit of zero is degenerate: the signal fires on every poll because
        # any count is >= 0. That is a self-inflicted denial of service rather
        # than a safety gain, so it is not "tightening".
        if value == 0:
            raise HTTPException(
                status_code=400,
                detail=f"{key}=0 would fire on every check; use 1 for the strictest setting",
            )
        current = float(getattr(watchdog, key))
        if value > current:
            decision = gate.decide(
                Action(category=ActionCategory.DISABLE_SAFETY, target=key),
                level,
            )
            if not decision.allowed:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"{key} can only be tightened here; loosening it from "
                        f"{current:g} to {value:g} is refused — {decision.reason}"
                    ),
                )
        setattr(watchdog, key, int(value) if key != "cpu_percent" else value)

    return {
        "cpu_percent": watchdog.cpu_percent,
        "cpu_streak_limit": watchdog.cpu_streak_limit,
        "deletion_limit": watchdog.deletion_limit,
        "pids": watchdog.pids,
        "trips": watchdog.trips,
        "policy": (
            "thresholds may only be tightened; loosening requires disable_safety, "
            "which is blocked at every trust level"
        ),
    }
