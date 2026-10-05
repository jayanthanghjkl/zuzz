"""Face API — memory routes.

What ZUZZ remembers: the profile, ranked recall, chronological history, and
forgetting.

**Forgetting is the endpoint that matters here, and it is deliberately awkward.**
``DELETE /api/memory/forget`` requires an explicit ``before`` date. There is no
"forget everything", no "forget this one conversation", and no default that
resolves to something destructive. Two reasons:

* an unreadable date is a **400**, never a silent "now". The one failure that
  matters here is a forget that quietly resolved a bad date to *everything* and
  emptied the store;
* the date is returned in the response along with the count, so the user can see
  what was about to go before it goes. ``dry_run`` defaults to True for the same
  reason — erasing is irreversible and a confirmation the user did not have to
  ask for is not a confirmation.

Unlike every other mutating route in the API, this one does not gate. Memory is
not an act on the machine, ``memory_access`` is ``auto`` at every trust level, and
the reasoning is in ``config/trust_levels.json``: prompting on every recall trains
people to click prompts through. The permission the row withholds is available
here instead — every stored item is listed, readable and erasable by hand.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from brain.hippocampus.exceptions import (
    MemoryRefused,
    MemoryStoreUnavailable,
    QueryTooShort,
)

router = APIRouter()

#: How many entries a history read returns when the caller does not say.
DEFAULT_HISTORY_LIMIT = 50

#: Hard ceiling on a recall or history response.
MAX_LIMIT = 200


class RecallRequest(BaseModel):
    """A request to find memories matching a question."""

    query: str = Field(..., description="What to look for")
    memory_type: str = Field("", description="conversation, task, preference, mistake or fact")
    limit: int = Field(10, description="Maximum memories to return")


class RememberRequest(BaseModel):
    """A request to remember something."""

    kind: str = Field(..., description="conversation, task, preference, mistake or fact")
    text: str = Field(..., description="What to remember")
    summary: str = Field("", description="One line to show in the list")
    key: str = Field("", description="The subject, for preferences and facts")
    value: str = Field("", description="The answer, for preferences")
    context: str = Field("", description="Where this came from")
    importance: float = Field(0.5, description="How worth keeping this is, 0.0 to 1.0")


class ForgetRequest(BaseModel):
    """A request to forget everything older than a date."""

    before: str = Field(..., description="ISO date or timestamp; nothing later is removed")
    dry_run: bool = Field(True, description="Report what would go without removing it")


def _memory(request: Request):
    """Return the Hippocampus wired into the app."""
    memory = getattr(request.app.state, "hippocampus", None)
    if memory is None:
        raise HTTPException(status_code=503, detail="memory is not attached to this app")
    return memory


def _bounded(limit: int, fallback: int) -> int:
    """Clamp a caller-supplied limit."""
    return max(1, min(int(limit), MAX_LIMIT)) if limit else fallback


def _refused(exc: Exception, tool: str) -> HTTPException:
    """Turn a memory refusal into the right status.

    Args:
        exc: The refusal.
        tool: The operation, for the detail message.

    Returns:
        A 503 for a store that could not be read, a 400 for everything else.
        Store-unavailable is genuinely transient — a locked file on Windows, a
        full disk — and telling the user their request was malformed would send
        them looking in the wrong place.
    """
    if isinstance(exc, MemoryStoreUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=f"{tool}: {exc}")


@router.get("/api/memory/profile")
async def profile(request: Request) -> Dict[str, Any]:
    """Return what ZUZZ knows about the user.

    Args:
        request: Used to reach the Hippocampus.

    Returns:
        The profile — name, preferences, per-kind counts and topics — plus a
        description of both memory tiers so the page can show what is held in RAM
        against what is on disk.
    """
    memory = _memory(request)
    return {
        "profile": memory.get_user_profile().to_dict(),
        "short_term": memory.short_term.stats(),
        "long_term": memory.long_term.stats(),
        "directory": str(memory.long_term.directory),
        "threshold": memory.consolidator.threshold,
    }


@router.post("/api/memory/recall")
async def recall(request: Request, body: RecallRequest) -> Dict[str, Any]:
    """Find memories matching a question.

    Args:
        request: Used to reach the Hippocampus.
        body: What to look for.

    Returns:
        Matching memories, most relevant first, in the short form that a list can
        show.

    Raises:
        HTTPException: 400 when the question has no searchable words or names an
            unknown memory kind, 503 when the store cannot be read.
    """
    memory = _memory(request)
    try:
        found = memory.recall_similar(
            body.query,
            type=body.memory_type or None,
            limit=_bounded(body.limit, 10),
        )
    except QueryTooShort as exc:
        raise _refused(exc, "recall") from exc
    except Exception as exc:
        raise _refused(exc, "recall") from exc
    return {
        "query": body.query,
        "count": len(found),
        "memories": [memory_entry.brief() for memory_entry in found],
    }


@router.post("/api/memory/remember")
async def remember(request: Request, body: RememberRequest) -> Dict[str, Any]:
    """Remember something.

    Args:
        request: Used to reach the Hippocampus.
        body: What to remember.

    Returns:
        The new memory, including its id and the score that decides whether it is
        kept permanently.

    Raises:
        HTTPException: 400 when the kind is unknown or a preference is missing its
            key or value.
    """
    memory = _memory(request)
    from brain.hippocampus.memory_types import Memory, coerce_kind

    try:
        kind = coerce_kind(body.kind)
        entry = Memory(
            kind=kind,
            summary=body.summary or body.text[:90],
            text=body.text,
            key=body.key,
            value=body.value,
            context=body.context or "api",
            importance=max(0.0, min(1.0, float(body.importance))),
            tags=[kind],
        )
    except MemoryRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    memory_id = memory.remember(entry)
    stored = memory.short_term.find(memory_id)
    request.app.state.logger.log(
        "hippocampus", "remember", kind=kind, memory_id=memory_id, context=body.context or "api"
    )
    return {
        "id": memory_id,
        "memory": stored.brief() if stored is not None else entry.brief(),
        "importance": stored.importance if stored is not None else entry.importance,
        "promoted": False,
        "note": "held in short-term memory; consolidate to make it permanent",
    }


@router.get("/api/memory/history")
async def history(
    request: Request,
    memory_type: str = "",
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> Dict[str, Any]:
    """List remembered items, newest first.

    Args:
        request: Used to reach the Hippocampus.
        memory_type: Restrict to one kind, or empty for all of them.
        limit: How many to return, capped at 200.

    Returns:
        The memories, newest first.

    Raises:
        HTTPException: 400 when the kind is not one ZUZZ keeps.
    """
    memory = _memory(request)
    try:
        found = memory.recall_history(
            limit=_bounded(limit, DEFAULT_HISTORY_LIMIT), type=memory_type or None
        )
    except Exception as exc:
        raise _refused(exc, "history") from exc
    return {
        "count": len(found),
        "memory_type": memory_type or "all",
        "memories": [entry.brief() for entry in found],
    }


@router.delete("/api/memory/forget")
async def forget(request: Request, body: Optional[ForgetRequest] = None) -> Dict[str, Any]:
    """Forget every memory older than a date.

    Args:
        request: Used to reach the Hippocampus and the activity log.
        body: The date, and whether this is a preview.

    Returns:
        How many memories matched, the date they were matched against, and whether
        anything was actually removed.

    Raises:
        HTTPException: 400 when no date was supplied or it cannot be read — never a
            silent "now", which would empty the store on a typo. 503 when the store
            cannot be rewritten.
    """
    if body is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "a forget needs an explicit date: pass before=YYYY-MM-DD to remove "
                "only memories older than that day"
            ),
        )
    memory = _memory(request)
    from brain.hippocampus.hippocampus import _as_timestamp

    try:
        cutoff = _as_timestamp(body.before)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.dry_run:
        matched = [
            entry
            for entry in memory.short_term.all() + memory.long_term.read()
            if entry.created < cutoff
        ]
        return {
            "dry_run": True,
            "before": body.before,
            "cutoff": cutoff,
            "would_forget": len(matched),
            "would_forget_ids": [entry.id for entry in matched],
            "note": "nothing was removed; re-send with dry_run=false to do it",
        }

    try:
        removed = memory.forget_before(cutoff)
    except MemoryStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    request.app.state.audit.record(
        "memory_forgotten",
        f"forgot {removed} memories older than {body.before}",
        before=str(body.before),
        cutoff=cutoff,
        removed=removed,
    )
    request.app.state.logger.log(
        "hippocampus", "forget", before=str(body.before), removed=removed
    )
    if removed == 0:
        return {
            "dry_run": False,
            "before": body.before,
            "cutoff": cutoff,
            "removed": 0,
            "note": "nothing was older than that date; the forget ran and matched nothing",
        }
    return {"dry_run": False, "before": body.before, "cutoff": cutoff, "removed": removed}


@router.post("/api/memory/consolidate")
async def consolidate(request: Request, dry_run: bool = True) -> Dict[str, Any]:
    """Promote the held memories worth keeping to disk.

    Args:
        request: Used to reach the Hippocampus and the activity log.
        dry_run: Report what would be promoted without writing.

    Returns:
        The promotion report, including the score threshold and what was skipped.
    """
    memory = _memory(request)
    report = memory.consolidate(dry_run=dry_run)
    request.app.state.logger.log(
        "hippocampus",
        "consolidate",
        promoted=report["promoted"],
        skipped=report["skipped"],
        dry_run=report["dry_run"],
    )
    return report


@router.get("/api/memory/candidates")
async def candidates(request: Request) -> Dict[str, Any]:
    """Explain what each held memory would score, and whether it is eligible.

    Args:
        request: Used to reach the Hippocampus.

    Returns:
        One entry per held memory, so the page can answer "why is this still in
        short-term memory?" without the user having to read the source.
    """
    memory = _memory(request)
    return {
        "threshold": memory.consolidator.threshold,
        "entries": memory.consolidator.score_all(),
    }