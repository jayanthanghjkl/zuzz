"""Face API — system-awareness routes.

The six reads the Parietal Lobe exposes, plus an ungated status endpoint. Each goes
through the permission gate before anything reaches the operating system; the flow
itself lives in :mod:`face.api.parietal_support` and is deliberately the same one the
vision routes use.

* ``GET /api/parietal/active-window`` — which window has focus.
* ``GET /api/parietal/screen-info`` — monitors, resolution, DPI.
* ``GET /api/parietal/running-apps`` — running applications and their resources.
* ``GET /api/parietal/mouse-position`` — where the pointer is.
* ``GET /api/parietal/context`` — what the user appears to be doing.
* ``GET /api/parietal/snapshot`` — all six at once.
* ``GET /api/parietal/status`` — the region's configuration and whether the platform
  will answer at all. **Not gated**, for the reason given in
  :mod:`face.api.parietal_support`: it is the panel a user consults to decide whether
  anything is worth granting.

These are all ``GET``. That is not a statement that they are harmless to call from a
browser's address bar without thought — they read the user's window titles, and on a
shared machine that is information. It is a statement that they change nothing, which
is what distinguishes them from the ``POST`` routes elsewhere in this API: a ``GET``
cannot re-target a click, and every one of these refuses to do anything but read.
Because they are ``GET``, their options arrive as query parameters — a ``GET`` with a
JSON body is not reliably supported and is not cacheable or bookmarkable — and they
are assembled by one helper so the six endpoints cannot drift on which fields they
accept.

The one endpoint that can answer wrongly rather than fail is ``/context``, so its
response always carries ``confidence``, ``reason`` and ``evidence`` — see
:mod:`brain.parietal_lobe.context_detector` for why ``unknown`` and ``idle`` are
different answers.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query, Request

from face.api.parietal_support import (
    ParietalRequest,
    lobe,
    preview,
    run,
)

router = APIRouter()


def _body(
    monitor: int = 0,
    confirmed: bool = False,
    confirmation_id: str = "",
) -> ParietalRequest:
    """Build the confirmation evidence a ``GET`` carried as query parameters.

    Args:
        monitor: Zero-based monitor index.
        confirmed: Whether the user approved a 202 prompt.
        confirmation_id: The id returned with the 202.

    Returns:
        The request object the shared flow expects. One helper rather than six copies
        so the endpoints cannot drift on which confirmation fields they accept.
    """
    return ParietalRequest(
        monitor=monitor, confirmed=confirmed, confirmation_id=confirmation_id
    )


@router.get("/api/parietal/active-window")
async def active_window(
    request: Request,
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Return the window that currently has focus.

    Args:
        request: The incoming request.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        The focused window with its title, application, rectangle and minimised
        state, or ``{"window": None}`` when nothing has focus.
    """
    region = lobe(request)
    body = _body(confirmed=confirmed, confirmation_id=confirmation_id)
    return await run(
        request,
        "active-window",
        lambda decision: region.get_active_window(decision),
        body,
        lambda: preview("active-window"),
    )


@router.get("/api/parietal/screen-info")
async def screen_info(
    request: Request,
    monitor: int = Query(0, description="Zero-based monitor index"),
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Return the display layout.

    Args:
        request: The incoming request.
        monitor: Zero-based monitor index.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        Resolution, the monitor list, DPI and scale, and whether this process is DPI
        aware — which is what decides whether the numbers are physical pixels.
    """
    region = lobe(request)
    body = _body(monitor, confirmed, confirmation_id)
    return await run(
        request,
        "screen-info",
        lambda decision: region.get_screen_info(decision),
        body,
        lambda: preview("screen-info", monitor),
        monitor=monitor,
    )


@router.get("/api/parietal/running-apps")
async def running_apps(
    request: Request,
    include_hidden: bool = Query(
        False, description="Also list processes with no visible window"
    ),
    limit: int = Query(50, description="Maximum applications to return"),
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Return the running applications.

    Args:
        request: The incoming request.
        include_hidden: Also list processes with no visible window.
        limit: Maximum applications to return.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        The application count, how many windowless processes were omitted, and the
        per-application records. ``include_hidden`` is off by default because the full
        process table is a fingerprint of the machine.
    """
    region = lobe(request)
    body = _body(confirmed=confirmed, confirmation_id=confirmation_id)

    async def execute(decision: Any) -> Dict[str, Any]:
        """List the applications the gate approved.

        Args:
            decision: The allowed decision.

        Returns:
            The application records.
        """
        return region.get_running_apps(
            decision, include_hidden=include_hidden, limit=limit
        )

    return await run(
        request,
        "running-apps",
        execute,
        body,
        lambda: preview("running-apps"),
    )


@router.get("/api/parietal/mouse-position")
async def mouse_position(
    request: Request,
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Return the pointer's position.

    Args:
        request: The incoming request.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        Coordinates, the monitor index, whether the point is on the desktop, and
        whether the cursor is visible — the last of which is False for a hidden cursor
        and must not be read as "the pointer is at the origin".
    """
    region = lobe(request)
    body = _body(confirmed=confirmed, confirmation_id=confirmation_id)
    return await run(
        request,
        "mouse-position",
        lambda decision: region.get_mouse_position(decision),
        body,
        lambda: preview("mouse-position"),
    )


@router.get("/api/parietal/context")
async def context(
    request: Request,
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Infer what the user is working on.

    Args:
        request: The incoming request.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        One of ``coding``, ``browsing``, ``writing``, ``reading``, ``gaming``,
        ``idle`` or ``unknown``, with a confidence, the rule that fired, the evidence
        and the OS input-idle reading.
    """
    region = lobe(request)
    body = _body(confirmed=confirmed, confirmation_id=confirmation_id)
    return await run(
        request,
        "detect-context",
        lambda decision: region.detect_user_context(decision),
        body,
        lambda: preview("detect-context"),
    )


@router.get("/api/parietal/snapshot")
async def snapshot(
    request: Request,
    confirmed: bool = Query(False, description="The user approved the 202 prompt"),
    confirmation_id: str = Query("", description="Id returned with the 202"),
) -> Dict[str, Any]:
    """Return every system-awareness signal at once.

    Args:
        request: The incoming request.
        confirmed: Whether a 202 prompt was approved.
        confirmation_id: The id returned with the 202.

    Returns:
        All six reads. Partial rather than all-or-nothing: a slot the platform would
        not answer carries ``{"error": ..., "failed": true}`` rather than causing the
        whole snapshot to fail, so losing ``psutil`` does not also lose the answer to
        "which window am I in".
    """
    region = lobe(request)
    body = _body(confirmed=confirmed, confirmation_id=confirmation_id)
    return await run(
        request,
        "snapshot",
        lambda decision: region.get_full_snapshot(decision),
        body,
        lambda: preview("snapshot"),
    )


@router.get("/api/parietal/status")
async def parietal_status(request: Request) -> Dict[str, Any]:
    """Return the region's configuration. Not gated.

    Args:
        request: Used to reach the app state.

    Returns:
        The category, the six operations, which optional libraries are absent, whether
        the layout can be read, and whether the kill switch is engaged.
    """
    region = lobe(request)
    return {"status": "ok", "result": region.status()}
