"""Body/Tools — memory.

Reading and writing what ZUZZ remembers, reached from a chat request.

Every tool here resolves to the ``memory_access`` trust category, which is ``auto``
at every level. That row is not an oversight and the reasoning is in
``config/trust_levels.json``: memory is local, and the alternative is a dialog every
time ZUZZ says "as you mentioned", which teaches people to click prompts through.
The permission the row withholds is available on demand instead — the Memory page
lists every stored item and can erase it.

**There is deliberately no tool that erases.** ``forget_before`` exists on the
orchestrator and on ``DELETE /api/memory/forget``, and it needs a real date. A
model being able to call it from an ordinary sentence would mean the store could be
emptied by a chat, and a machine that can be made to forget whatever was just said
to it is not one that remembers anything. Erasing is something the user does, from
a page that shows them what they are about to lose.

**Recall returns the short form.** :meth:`Memory.brief` rather than the body,
because a recall list carrying every stored transcript would be a page of text
rather than a list of things. The ids are returned so the caller can ask for more
if it needs to.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from body.tools.tool_spec import (
    ToolContext,
    ToolRefused,
    ToolSpec,
    ToolTarget,
    required_text,
)
from brain.hippocampus.exceptions import MemoryRefused

#: The trust category every memory tool resolves to.
CATEGORY = "memory_access"

#: How many results a recall returns when the model does not say.
DEFAULT_LIMIT = 10

#: Hard ceiling on results. The schema's integer type allows anything, and a model
#: asking for 100000 results would otherwise serialise the entire store into a
#: chat reply.
MAX_LIMIT = 50


def _memory(context: ToolContext, tool: str):
    """Return the Hippocampus, refusing when the region is not attached.

    Args:
        context: The collaborators the tool may act through.
        tool: The tool name, for the message.

    Returns:
        The Hippocampus.

    Raises:
        ToolRefused: When ``context.hippocampus`` is None. Answering "you have no
            preferences" from a store that was never attached would be a confident
            wrong answer, which is the failure the other tool families in this
            package all guard against.
    """
    hippocampus = context.hippocampus
    if hippocampus is None:
        raise ToolRefused(
            f"{tool}: memory is not attached, so ZUZZ cannot read or write it. "
            "Say so rather than answering from an empty store."
        )
    return hippocampus


def _limit(params: Dict[str, Any]) -> int:
    """Return a bounded result count from validated parameters."""
    try:
        requested = int(params.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        requested = DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


def _memory_type(params: Dict[str, Any]) -> Optional[str]:
    """Return the requested memory kind, or None for all of them."""
    value = str(params.get("memory_type", "") or "").strip()
    return value or None


def _refusal(exc: MemoryRefused, tool: str) -> ToolRefused:
    """Re-raise a memory refusal as a tool precheck refusal.

    The message is carried across verbatim rather than summarised: "that question
    has no searchable words in it" is more use to the user than "memory error",
    and this happens before any decision exists, so it belongs in the precheck
    channel.
    """
    return ToolRefused(f"{tool}: {exc}")


# -- memory_recall_similar ---------------------------------------------------


def _resolve_recall(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a recall into the query the gate judges."""
    query = required_text(params, "query", "memory_recall_similar")
    memory = _memory(context, "memory_recall_similar")
    try:
        memory.recall_similar(query, type=_memory_type(params), limit=_limit(params))
    except MemoryRefused as exc:
        raise _refusal(exc, "memory_recall_similar") from exc
    return ToolTarget(
        category=CATEGORY,
        target=query[:120],
        preview={"description": f"Search memory for “{query[:120]}”"},
        detail={"memory_type": _memory_type(params)},
    )


def _execute_recall(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Run the recall and return the short form of each hit."""
    query = str(params["query"])
    found = _memory(context, "memory_recall_similar").recall_similar(
        query, type=_memory_type(params), limit=_limit(params)
    )
    return {
        "query": query,
        "count": len(found),
        "memories": [memory.brief() for memory in found],
    }


# -- memory_remember_fact ----------------------------------------------------


def _resolve_remember_fact(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a fact into the subject the gate judges."""
    fact = required_text(params, "fact", "memory_remember_fact")
    _memory(context, "memory_remember_fact")
    return ToolTarget(
        category=CATEGORY,
        target=fact[:120],
        preview={"description": f"Remember: {fact[:120]}"},
        detail={"kind": "fact"},
    )


def _execute_remember_fact(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Store the fact in short-term memory."""
    fact = str(params["fact"])
    origin = str(params.get("context", "") or "")
    memory_id = _memory(context, "memory_remember_fact").remember_fact(
        fact, context=origin or "chat"
    )
    return {
        "id": memory_id,
        "kind": "fact",
        "stored": "short_term",
        "note": (
            "held in memory; it becomes permanent once consolidation runs, "
            "which you can trigger from the Memory page"
        ),
    }


# -- memory_remember_preference ---------------------------------------------


def _resolve_remember_preference(
    params: Dict[str, Any], context: ToolContext
) -> ToolTarget:
    """Resolve a preference into the subject the gate judges."""
    key = required_text(params, "key", "memory_remember_preference")
    value = required_text(params, "value", "memory_remember_preference")
    _memory(context, "memory_remember_preference")
    return ToolTarget(
        category=CATEGORY,
        target=f"{key}: {value}"[:120],
        preview={"description": f"Remember that {key} is {value}"[:200]},
        detail={"kind": "preference"},
    )


def _execute_remember_preference(
    decision: Any, params: Dict[str, Any], context: ToolContext
) -> Any:
    """Store the preference in short-term memory."""
    key = str(params["key"])
    value = str(params["value"])
    memory_id = _memory(context, "memory_remember_preference").remember_preference(
        key, value, context="chat"
    )
    return {"id": memory_id, "kind": "preference", "key": key.lower(), "value": value}


# -- memory_search_history ---------------------------------------------------


def _resolve_search_history(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a history read. The target is fixed: there is nothing to name."""
    _memory(context, "memory_search_history")
    query = str(params.get("query", "") or "").strip()
    return ToolTarget(
        category=CATEGORY,
        target=query[:120] if query else "all recent memory",
        preview={
            "description": (
                f"Search memory for “{query[:120]}”" if query else "List remembered items, newest first"
            )
        },
        detail={"memory_type": _memory_type(params)},
    )


def _execute_search_history(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the memories, newest first — or ranked when a query is given.

    Both modes are here rather than one, because "what have we done" and "what did
    we do about the parser" are different questions and answering the second with the
    first's ordering puts the answer at the bottom of a list nobody scrolls to. With
    no query this stays the chronological view the History panel is built on.
    """
    memory = _memory(context, "memory_search_history")
    query = str(params.get("query", "") or "").strip()
    kind = _memory_type(params)
    if query:
        found = memory.recall_similar(query, type=kind, limit=_limit(params))
        ranked = True
    else:
        found = memory.recall_history(limit=_limit(params), type=kind)
        ranked = False
    return {
        "query": query,
        "ranked": ranked,
        "count": len(found),
        "memories": [memory_item.brief() for memory_item in found],
    }


# -- memory_get_user_profile -------------------------------------------------


def _resolve_user_profile(params: Dict[str, Any], context: ToolContext) -> ToolTarget:
    """Resolve a profile read. Reading the user is not naming a target."""
    _memory(context, "memory_get_user_profile")
    return ToolTarget(
        category=CATEGORY,
        target="the user profile",
        preview={"description": "Read what ZUZZ knows about the user"},
        detail={"kind": "profile"},
    )


def _execute_user_profile(decision: Any, params: Dict[str, Any], context: ToolContext) -> Any:
    """Return the user profile as stored."""
    return _memory(context, "memory_get_user_profile").get_user_profile().to_dict()


#: The memory tools.
MEMORY_TOOLS: tuple = (
    ToolSpec(
        name="memory_recall_similar",
        summary="Search what ZUZZ remembers",
        resolve=_resolve_recall,
        execute=_execute_recall,
        returns="Matching memories, most relevant first",
    ),
    ToolSpec(
        name="memory_remember_fact",
        summary="Remember a durable fact",
        resolve=_resolve_remember_fact,
        execute=_execute_remember_fact,
        returns="The new memory's id",
    ),
    ToolSpec(
        name="memory_remember_preference",
        summary="Remember a standing preference",
        resolve=_resolve_remember_preference,
        execute=_execute_remember_preference,
        returns="The new memory's id",
    ),
    ToolSpec(
        name="memory_search_history",
        summary="List remembered items newest first",
        resolve=_resolve_search_history,
        execute=_execute_search_history,
        returns="Recent memories, unranked",
    ),
    ToolSpec(
        name="memory_get_user_profile",
        summary="Read what ZUZZ knows about the user",
        resolve=_resolve_user_profile,
        execute=_execute_user_profile,
        returns="The user's name, preferences and topics",
    ),
)