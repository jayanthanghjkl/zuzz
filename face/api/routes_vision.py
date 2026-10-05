"""Face API — vision routes.

The five endpoints the Occipital Lobe exposes, plus a status read and a way to fetch
a stored screenshot. All of them are routed through the permission gate before
anything reaches the screen; the flow itself lives in
:mod:`face.api.vision_support` and is the same one the file and desktop routes use.

* ``POST /api/vision/capture`` — take a screenshot, optionally classify and store it.
* ``POST /api/vision/describe`` — say what is on the screen.
* ``POST /api/vision/find-element`` — locate a UI element by description.
* ``POST /api/vision/ocr`` — transcribe a region.
* ``POST /api/vision/verify`` — compare two stored screenshots, or a stored one
  against the live screen.
* ``GET /api/vision/status`` — thresholds, monitors, model and budget state.
* ``GET /api/vision/screenshots`` and ``/api/vision/screenshot/{name}`` — the stored
  frames.

The two reads are **not** gated, for the same reason the trust level and the VRAM
gauges are not: they are the panels a user consults in order to decide whether to
grant a permission. A gauge that needed a confirmation would be a gauge nobody looked
at.

Fetching a screenshot's *contents* is a different matter from listing it. The listing
names files; the fetch returns a frame of the user's desktop, which is exactly what
``visual_capture`` exists to gate. So the fetch requires a decision, and refuses a
name that is not a bare file name inside the store.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from core.audit import SCREENSHOT_READ

from face.api.vision_support import (
    CaptureRequest,
    DescribeRequest,
    FindRequest,
    ReadRequest,
    VerifyRequest,
    audit,
    lobe,
    preview,
    read_preview,
    run,
)

router = APIRouter()

#: Which file extension the store serves. Screenshots are PNG and nothing else is
#: written there, so anything else in the directory is not ours to hand out.
SUFFIX = ".png"


@router.post("/api/vision/capture")
async def capture(request: Request, body: CaptureRequest) -> Dict[str, Any]:
    """Take a screenshot, and optionally classify and store it.

    Args:
        request: The incoming request.
        body: Monitor, region and storage options.

    Returns:
        The capture's metadata, and its stored path when ``store`` was set.
    """
    region = lobe(request)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Run the capture the gate approved.

        Args:
            decision: The allowed decision.

        Returns:
            The capture, or the classified and stored record.
        """
        if not body.store:
            return region.capture(decision, body.monitor, body.region, body.label).summary()
        return await region.capture_and_store(
            decision,
            body.monitor,
            body.region,
            body.label,
            body.acknowledge_sensitive,
            body.consult_model,
        )

    return await run(
        request,
        "capture",
        body.monitor,
        body.region,
        body,
        execute,
        lambda: preview("capture", body.monitor, body.region),
    )


@router.post("/api/vision/describe")
async def describe(request: Request, body: DescribeRequest) -> Dict[str, Any]:
    """Say what is on the screen.

    Args:
        request: The incoming request.
        body: Monitor, region and an optional focus.

    Returns:
        The description, marked untrusted, with its confidence and threshold verdict.
    """
    region = lobe(request)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Ask the vision model what is on the screen.

        Args:
            decision: The allowed decision.

        Returns:
            The description record.
        """
        return await region.describe(decision, body.monitor, body.region, body.question)

    return await run(
        request,
        "describe",
        body.monitor,
        body.region,
        body,
        execute,
        lambda: preview("describe", body.monitor, body.region),
    )


@router.post("/api/vision/find-element")
async def find_element(request: Request, body: FindRequest) -> Dict[str, Any]:
    """Locate a UI element and return its coordinates.

    Args:
        request: The incoming request.
        body: The element description, monitor and region.

    Returns:
        The element record: status, confidence, box, centre and normalised box.
    """
    region = lobe(request)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Ask the vision model to locate the element.

        Args:
            decision: The allowed decision.

        Returns:
            The element record.
        """
        return await region.find_element(decision, body.description, body.monitor, body.region)

    return await run(
        request,
        "find",
        body.monitor,
        body.region,
        body,
        execute,
        lambda: preview("find", body.monitor, body.region),
    )


@router.post("/api/vision/ocr")
async def ocr(request: Request, body: DescribeRequest) -> Dict[str, Any]:
    """Transcribe the text in a region.

    Args:
        request: The incoming request.
        body: Monitor and region.

    Returns:
        The OCR record, with the text marked untrusted.
    """
    region = lobe(request)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Read the region's text.

        Args:
            decision: The allowed decision.

        Returns:
            The OCR record.
        """
        return await region.read_text(decision, body.region, body.monitor)

    return await run(
        request,
        "ocr",
        body.monitor,
        body.region,
        body,
        execute,
        lambda: preview("ocr", body.monitor, body.region),
    )


@router.post("/api/vision/verify")
async def verify(request: Request, body: VerifyRequest) -> Dict[str, Any]:
    """Compare two screenshots and report whether the screen changed.

    Args:
        request: The incoming request.
        body: Two screenshot names, or ``current`` for the live screen.

    Returns:
        The comparison report, or 404 when a name is not a stored screenshot.
    """
    region = lobe(request)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Compare the two sides.

        Args:
            decision: The allowed decision.

        Returns:
            The comparison report.
        """
        return region.compare(
            decision,
            _source(region, decision, body.before, body.monitor),
            _source(region, decision, body.after, body.monitor),
            body.label,
            monitor=body.monitor,
        )

    return await run(
        request,
        "verify",
        body.monitor,
        None,
        body,
        execute,
        lambda: preview("verify", body.monitor, None),
    )


@router.get("/api/vision/status")
async def status(request: Request) -> Dict[str, Any]:
    """Return the region's configuration. Not gated; a gauge, not an action."""
    return lobe(request).status()


@router.get("/api/vision/screenshots")
async def screenshots(request: Request) -> Dict[str, Any]:
    """List the stored screenshots. Not gated; it names files, not contents."""
    entries = lobe(request).stored()
    return {"count": len(entries), "screenshots": entries}


@router.get("/api/vision/screenshot/{name}")
async def screenshot(
    request: Request, name: str, confirmed: bool = False, confirmation_id: str = ""
) -> Any:
    """Return one stored screenshot. Gated: it is a frame of the user's desktop.

    Args:
        request: The incoming request.
        name: A bare file name, as returned by the store.
        confirmed: The user approved the 202 prompt.
        confirmation_id: Id returned with the 202.

    Returns:
        The PNG, or the 202 confirmation prompt.

    Raises:
        HTTPException: 404 for a name that is not a stored screenshot, 403 when the
            gate refuses, 409 while the kill switch is engaged.
    """
    region = lobe(request)
    store = region.engine.store
    try:
        path = store.resolve(name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if path.suffix != SUFFIX:
        raise HTTPException(status_code=404, detail=f"{name} is not a screenshot")
    body = ReadRequest(confirmed=confirmed, confirmation_id=confirmation_id)

    async def execute(decision: Any) -> Dict[str, Any]:
        """Return the resolved path, which the route then serves.

        The read has already been performed by :meth:`ScreenshotStore.resolve`, which
        only checks the name; the bytes leave the process only through this response,
        so the decision is enforced before the file is opened. Recording the read here
        means the audit log shows which frame was handed out.

        Args:
            decision: The allowed decision.

        Returns:
            The path to serve.
        """
        audit(request, SCREENSHOT_READ, path.name, decision)
        return {"path": path}

    gated = await run(
        request,
        "read-screenshot",
        0,
        None,
        body,
        execute,
        lambda: read_preview(path.name),
        action=region.propose_read(path.name),
    )
    # The gate answered "ask" and returned the 202 confirmation prompt instead of a
    # result. That response is returned as it is: a JSONResponse is not a subscriptable
    # envelope, so treating it as one is a TypeError, and — worse — a route that
    # caught that and served an empty file would hand out a frame the user never
    # approved. The same prompt the other five endpoints return.
    if isinstance(gated, JSONResponse):
        return gated
    return FileResponse(gated["result"]["path"], media_type="image/png")


def _source(region: Any, decision: Any, name: str, monitor: int = 0) -> Any:
    """Return what one side of a comparison refers to.

    Args:
        region: The Occipital Lobe.
        decision: The allowed ``verify`` decision.
        name: A stored screenshot name, or ``current``.
        monitor: Zero-based monitor index, used when the side is the live screen.

    Returns:
        A :class:`~brain.occipital_lobe.capture.Capture` for ``current``, or a
        resolved path to the stored file.

    Raises:
        HTTPException: 404 when the name is not a stored screenshot.
    """
    if str(name).strip().lower() in ("current", "live", ""):
        return region.capture(decision, monitor, None, label="verify")
    try:
        return region.engine.store.resolve(name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc