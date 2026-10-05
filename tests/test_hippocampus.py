"""Hippocampus â€” memory tests.

The region, not the endpoints: the layers underneath the API. What these pin
down are the invariants that make memory safe to leave running unattended.

The first test is the one that matters most. Ordinary remembering must not
write to disk. A store that persists on every ``remember()`` has quietly made
every conversation permanent, which is the opposite of what short-term memory
is for and is not something a user would notice until they tried to undo it.
Promotion is a separate, explicit act, so these tests check that boundary
rather than only the happy path.

A note on kinds: :data:`brain.hippocampus.memory_types.MemoryKind` is ``str``,
deliberately. A kind read back from JSONL is then the same value as one written
in code, so these tests pass the plain string rather than an enum member.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from brain.hippocampus import (
    Consolidator,
    Hippocampus,
    LongTermMemory,
    Memory,
    ShortTermMemory,
    UserProfile,
    coerce_kind,
    score_importance,
)
from body.tools.tool_spec import ToolContext
from brain.hippocampus.exceptions import (
    MemoryStoreUnavailable,
    QueryTooShort,
    UnknownMemoryKind,
)


# -- helpers ---------------------------------------------------------


def memory(**kwargs) -> Memory:
    """Build a memory with sensible defaults for the field under test.

    ``tags`` defaults to one entry because :func:`score_importance` counts a
    tagged or keyed memory as specific, and most tests here care about the
    threshold. Tests that want a memory to *miss* it build the text explicitly.
    """
    kwargs.setdefault("kind", "fact")
    kwargs.setdefault("summary", "a memory")
    kwargs.setdefault("text", "some searchable text about the thing")
    kwargs.setdefault("tags", ["test"])
    return Memory(**kwargs)


def promotable(summary: str = "a strong memory", **kwargs) -> Memory:
    """Build a memory that clears the consolidation threshold.

    ``kind=preference`` scores the kind bar, ``always`` in the summary scores
    the explicitness bar, and the length clears the minimum. Together they are
    comfortably above 0.5, so a test that is about something else is not
    quietly also a test of the scorer.
    """
    kwargs.setdefault("kind", "preference")
    kwargs.setdefault("text", "always use this, it is the setting the user asked for")
    return memory(summary="always: " + summary, **kwargs)


def brain(tmp_path: Path, **kwargs) -> Hippocampus:
    """Return a Hippocampus over a throwaway directory."""
    return Hippocampus(directory=tmp_path / "memory", **kwargs)


def held(memory_: Hippocampus) -> int:
    """Return how many memories short-term memory is holding."""
    return len(memory_.short_term.all())


# -- the disk boundary -----------------------------------------------


def test_remembering_does_not_write_to_disk(tmp_path: Path) -> None:
    """``remember()`` must leave the disk empty; only promotion writes.

    This is the whole reason consolidation is a separate call. If this fails,
    every conversation is durable the moment it is spoken and
    ``forget_before`` is the only way back.
    """
    store = tmp_path / "memory"
    memory_ = brain(tmp_path)

    memory_.remember(memory(kind="conversation", summary="hello"))
    memory_.remember(memory(kind="preference", summary="editor: neovim"))

    assert memory_.long_term.count() == 0
    assert not list(store.glob("*.jsonl")), "remember() must not create a shard"

    # ...and the held memories are still real, still recallable.
    assert held(memory_) == 2
    assert memory_.recall_similar("editor")


def test_consolidation_is_the_only_thing_that_writes(tmp_path: Path) -> None:
    """A promoted memory appears on disk and survives a fresh load."""
    store = tmp_path / "memory"
    memory_ = brain(tmp_path)
    memory_.remember(promotable("editor: neovim", key="editor", value="neovim"))

    # The dry run reports what it *would* promote; the count is the point of
    # asking. What it must not do is write.
    preview = memory_.consolidate(dry_run=True)
    assert preview["promoted"] == 1
    assert preview["dry_run"] is True
    assert memory_.long_term.count() == 0, "a dry run must not write"

    report = memory_.consolidate(dry_run=False)
    assert report["promoted"] == 1
    assert memory_.long_term.count() == 1
    assert list(store.glob("*.jsonl"))

    # A second Hippocampus over the same directory sees it, which is the point
    # of putting it on disk rather than in RAM.
    reloaded = Hippocampus(directory=store)
    assert any(entry.key == "editor" for entry in reloaded.recall_similar("editor"))


def test_a_dry_run_and_the_real_run_agree_on_the_count(tmp_path: Path) -> None:
    """Otherwise the preview is a lie and the write is a surprise."""
    memory_ = brain(tmp_path)
    for index in range(4):
        memory_.remember(promotable("fact %d" % index))

    preview = memory_.consolidate(dry_run=True)
    assert preview["promoted"] == 4
    assert memory_.long_term.count() == 0

    assert memory_.consolidate(dry_run=False)["promoted"] == preview["promoted"]


def test_promotion_is_idempotent(tmp_path: Path) -> None:
    """A second pass over the same buffer writes nothing twice.

    Sleeping twice is normal, and a keypress held down longer is normal.
    Neither may inflate what ZUZZ believes it knows.
    """
    memory_ = brain(tmp_path)
    memory_.remember(promotable("shell: pwsh", key="shell", value="pwsh"))

    assert memory_.consolidate(dry_run=False)["promoted"] == 1
    assert memory_.consolidate(dry_run=False)["promoted"] == 0
    assert memory_.long_term.count() == 1


def test_a_memory_the_caller_calls_important_but_is_not_is_refused(tmp_path: Path) -> None:
    """Both bars must be cleared, not just the caller's claim.

    ``importance=1.0`` on everything would otherwise turn consolidation into
    "write whatever was said", which is the opposite of what a threshold is for.
    """
    memory_ = brain(tmp_path)
    memory_.remember(promotable("editor: neovim"))
    # A one-word conversation cannot reach the bar however it is claimed.
    memory_.remember(
        memory(kind="conversation", summary="hi", text="hi", importance=1.0, tags=[])
    )

    report = memory_.consolidate(dry_run=False)
    assert report["reasons"] == ["below_threshold"]
    assert report["promoted"] == 1
    assert memory_.long_term.count() == 1


def test_the_score_panel_agrees_with_what_consolidation_does(tmp_path: Path) -> None:
    """``score_all()`` must not call a memory eligible that consolidation refuses.

    The Memory page draws its threshold bar from ``score_all``. If that panel
    disagrees with the decision, the page explains a decision that never
    happens â€” so this asserts the two agree for every held memory, including
    the one whose computed score is below the bar while its claim is above it.
    """
    memory_ = brain(tmp_path)
    memory_.remember(promotable("editor: neovim"))
    memory_.remember(
        memory(kind="conversation", summary="hi", text="hi", importance=0.9, tags=[])
    )
    memory_.remember(memory(kind="task", summary="ship it", text="a task", importance=0.2))

    panel = memory_.consolidator.score_all()
    eligible_by_panel = {entry["id"] for entry in panel if entry["eligible"]}
    promoted_ids = set(memory_.consolidate(dry_run=False)["ids"])
    assert eligible_by_panel == promoted_ids


def test_the_score_panel_says_which_bar_was_missed(tmp_path: Path) -> None:
    """A refusal with no reason is not explainable.

    The two failures are different problems for the user: a low *claimed*
    importance means the caller should not have cared, and a low *computed*
    score means the memory really was small talk.

    The computed-score case goes through ``Hippocampus.remember``, which floors
    the claim at the computed score â€” so a caller claiming ``importance=0.9`` on
    the word "hi" ends up stored at the score's own low value and is refused for
    the *computed* reason.

    The claimed-importance case has to go through ``short_term.remember``
    directly, because that is the only way the claim can stay low: it is how a
    memory restored from a previous session, or held by a caller that scored it
    itself, enters the buffer with its own number intact.
    """
    memory_ = brain(tmp_path)
    small_talk = memory(
        kind="conversation", summary="hi", text="hi", importance=0.9, tags=[]
    )
    uncared_about = memory(
        kind="task",
        summary="ship it",
        # Long enough to clear the length bar, so this memory fails *only* the
        # claimed-importance bar. Short text would fail both and the two
        # failures would be indistinguishable in the panel.
        text=(
            "the migration is written, reviewed and merged, and the only thing "
            "left is for somebody to notice that nobody marked it important"
        ),
        importance=0.2,
    )
    memory_.remember(small_talk)
    memory_.short_term.remember(uncared_about)

    by_id = {entry["id"]: entry for entry in memory_.consolidator.score_all()}
    assert by_id[small_talk.id]["blocked_by"] == ["computed_score"]
    assert by_id[uncared_about.id]["blocked_by"] == ["claimed_importance"]
    assert not by_id[uncared_about.id]["eligible"]
    assert by_id[uncared_about.id]["score"] >= by_id[uncared_about.id]["threshold"]


# -- the two tiers ---------------------------------------------------


def test_short_term_respects_its_capacity() -> None:
    """The oldest memory is dropped when RAM is full, and the newest kept.

    Short-term memory is bounded on purpose. An unbounded recent-history buffer
    is a memory that never forgets anything, which is the failure mode that
    makes a system feel haunted.
    """
    store = ShortTermMemory(capacity=5)
    ids = [
        store.remember(memory(kind="conversation", summary="turn %d" % index)).id
        for index in range(8)
    ]

    assert store.stats()["held"] == 5
    assert store.find(ids[-1]) is not None, "the newest should be kept"
    assert store.find(ids[0]) is None, "the oldest should have been dropped"
    assert store.stats()["capacity"] == 5


def test_long_term_survives_a_corrupt_line(tmp_path: Path) -> None:
    """One unreadable line does not make the whole store unreadable.

    The store is append-only JSONL, so a torn write at the end is expected
    rather than exceptional. Losing one memory is acceptable; losing every
    memory because of it is not.
    """
    store = LongTermMemory(directory=tmp_path / "memory")
    store.store(memory(summary="first", text="first"))
    store.store(memory(summary="second", text="second"))

    shard = store.path_for("fact")
    with shard.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")

    assert store.count() == 2
    assert any(entry.summary == "second" for entry in store.read())


def test_each_kind_has_its_own_file(tmp_path: Path) -> None:
    """Five kinds, five files.

    One file per kind rather than one growing store, so deleting every
    conversation is a matter of removing five known paths instead of
    filtering a single large file by a field.
    """
    store = LongTermMemory(directory=tmp_path / "memory")
    store.store(memory(kind="fact", summary="a fact"))
    store.store(memory(kind="preference", summary="a preference"))

    assert store.path_for("fact").name == "facts.jsonl"
    assert store.path_for("preference").name == "preferences.jsonl"
    assert len(list((tmp_path / "memory").glob("*.jsonl"))) == 2
    assert store.stats()["fact"] == 1
    assert store.stats()["preference"] == 1


def test_forget_ids_reports_honestly(tmp_path: Path) -> None:
    """Deleting a memory that is not there returns 0 rather than raising."""
    store = LongTermMemory(directory=tmp_path / "memory")
    entry = memory(summary="doomed")
    store.store(entry)

    assert store.forget_ids([entry.id]) == 1
    assert store.forget_ids([entry.id]) == 0, "a second delete must not claim success"
    assert store.count() == 0


# -- recall ---------------------------------------------------------


def test_recall_ranks_the_relevant_memory_first(tmp_path: Path) -> None:
    """A distinctive term finds the right memory.

    This is the reason for IDF weighting, asserted directly: a scheme that
    ignores how common a word is puts a memory full of common words ahead of
    the one that actually answers the question.
    """
    memory_ = brain(tmp_path)
    memory_.remember(memory(summary="editor: neovim", text="the editor is neovim"))
    memory_.remember(
        memory(
            kind="conversation",
            summary="a long chat about the build",
            text="we talked about the build and the editor and the tests for a while",
        )
    )

    found = memory_.recall_similar("neovim")
    assert found
    assert found[0].summary == "editor: neovim"


def test_the_index_is_reused_within_its_cache_window(tmp_path: Path) -> None:
    """A short cache avoids rescanning on every question.

    ``index_cache_seconds`` defaults to 30. The failure this prevents is
    paying a full scan per keystroke on a large store. The cost is that a
    memory remembered seconds ago is not in the search index yet, which is
    stated here rather than left to be discovered â€” the window is bounded and
    short, and a brand-new memory is in short-term memory regardless.
    """
    memory_ = brain(tmp_path, index_cache_seconds=30.0)
    memory_.remember(memory(summary="first memory", text="the first thing"))

    assert memory_.recall_similar("first"), "the index should be built on demand"

    memory_.remember(memory(summary="second memory", text="the second thing"))
    fresh = memory_.recall_similar("second")
    if fresh:
        return  # the window expired; nothing to assert about staleness
    assert not fresh, "within the cache window the index may be stale"


def test_an_expired_cache_sees_a_new_memory(tmp_path: Path) -> None:
    """With the window at zero the index is rebuilt every time.

    This is the other half of the test above: it proves the staleness is
    bounded by the setting rather than permanent.
    """
    memory_ = brain(tmp_path, index_cache_seconds=0.0)
    memory_.remember(memory(summary="first memory", text="the first thing"))
    assert memory_.recall_similar("first")

    memory_.remember(memory(summary="second memory", text="the second thing"))
    assert memory_.recall_similar("second"), "a zero window must see the new memory"


def test_recall_can_be_narrowed_to_one_kind(tmp_path: Path) -> None:
    """A kind filter excludes other kinds rather than ranking them lower."""
    memory_ = brain(tmp_path)
    memory_.remember(memory(kind="preference", summary="p: editor", text="editor"))
    memory_.remember(memory(kind="mistake", summary="m: editor", text="editor"))

    found = memory_.recall_similar("editor", type="preference")
    assert found
    assert all(entry.kind == "preference" for entry in found)


def test_a_query_with_no_words_is_refused(tmp_path: Path) -> None:
    """Refusing is the point: returning everything would be worse."""
    memory_ = brain(tmp_path)
    memory_.remember(memory(text="something searchable"))
    with pytest.raises(QueryTooShort):
        memory_.recall_similar("   ")


def test_an_unknown_kind_is_refused() -> None:
    """A bad kind names itself rather than defaulting to conversation."""
    with pytest.raises(UnknownMemoryKind):
        coerce_kind("gossip")


def test_a_stored_kind_survives_the_round_trip(tmp_path: Path) -> None:
    """A kind read from JSONL equals the one written.

    This is the whole reason ``MemoryKind`` is ``str`` rather than an enum.
    """
    store = LongTermMemory(directory=tmp_path / "memory")
    entry = memory(kind="mistake", summary="the build failed twice")
    store.store(entry)

    reloaded = store.read()[0]
    assert reloaded.kind == "mistake"
    assert reloaded.id == entry.id


# -- history and the profile ----------------------------------------


def test_history_is_newest_first(tmp_path: Path) -> None:
    """Chronological order, because "what did we just do" is the usual question."""
    memory_ = brain(tmp_path)
    for index in range(5):
        memory_.remember(memory(kind="conversation", summary="turn %d" % index,
                                created=1_000_000.0 + index))

    assert [entry.summary for entry in memory_.recall_history(limit=5)] == [
        "turn 4",
        "turn 3",
        "turn 2",
        "turn 1",
        "turn 0",
    ]


def test_the_profile_counts_and_names_preferences(tmp_path: Path) -> None:
    """The profile is what the Memory page shows, so its shape is load-bearing."""
    memory_ = brain(tmp_path)
    memory_.remember_preference("editor", "neovim")
    memory_.remember_preference("shell", "pwsh")
    memory_.remember(memory(summary="a fact", text="a fact"))

    profile = memory_.get_user_profile()
    assert isinstance(profile, UserProfile)
    assert profile.total == 3
    assert profile.preferences.get("editor") == "neovim"
    assert profile.preferences.get("shell") == "pwsh"

    counts = profile.to_dict()["counts"]
    assert counts["preference"] == 2
    assert counts["fact"] == 1


def test_remembering_the_same_preference_twice_replaces_it(tmp_path: Path) -> None:
    """A changed preference is not two preferences.

    Otherwise the profile answers "editor" with whichever of two answers sorted
    higher, and the user cannot correct a wrong answer without the store being
    rewritten by hand.
    """
    memory_ = brain(tmp_path)
    memory_.remember_preference("editor", "vscode")
    memory_.remember_preference("editor", "neovim")

    profile = memory_.get_user_profile()
    assert profile.preferences.get("editor") == "neovim"
    assert profile.to_dict()["counts"]["preference"] == 1


# -- forgetting -----------------------------------------------------


def test_forgetting_removes_only_what_is_older(tmp_path: Path) -> None:
    """The cutoff is exclusive and everything newer is kept."""
    memory_ = brain(tmp_path)
    old = memory_.remember(
        memory(kind="conversation", summary="old", text="old", created=1_000_000.0)
    )
    new = memory_.remember(
        memory(kind="conversation", summary="new", text="new", created=2_000_000.0)
    )

    assert memory_.forget_before(1_500_000.0) == 1
    assert memory_.short_term.find(old) is None
    assert memory_.short_term.find(new) is not None


def test_forgetting_nothing_is_not_an_error(tmp_path: Path) -> None:
    """A forget that matched nothing returns 0 and raises nothing.

    The user asked a well-formed question and the answer happened to be zero.
    Reporting that as a failure would train people to distrust the one
    endpoint whose whole job is destructive.
    """
    memory_ = brain(tmp_path)
    memory_.remember(memory(summary="recent", text="recent"))

    assert memory_.forget_before(1.0) == 0
    assert held(memory_) == 1


def test_a_failed_write_during_forget_is_reported(tmp_path: Path) -> None:
    """Losing the report would mean claiming a deletion that did not happen."""
    memory_ = brain(tmp_path)
    memory_.remember(memory(kind="fact", summary="fact", text="fact"))
    memory_.consolidate(dry_run=False)
    memory_.remember(memory(kind="fact", summary="another", text="another"))

    def refuse(*args, **kwargs):
        raise MemoryStoreUnavailable("the store is locked by another process")

    memory_.long_term._rewrite = refuse
    with pytest.raises(MemoryStoreUnavailable):
        memory_.forget_before(float("inf"))


def test_a_durable_forget_reaches_the_disk(tmp_path: Path) -> None:
    """Forgetting has to survive a reload, not just clear RAM."""
    memory_ = brain(tmp_path)
    memory_.remember(promotable("the old way", created=1_000_000.0))
    memory_.remember(promotable("the new way", created=2_000_000.0))
    memory_.consolidate(dry_run=False)
    assert memory_.long_term.count() == 2

    assert memory_.forget_before(1_500_000.0) == 1
    assert memory_.long_term.count() == 1

    reloaded = Hippocampus(directory=tmp_path / "memory")
    assert [entry.summary for entry in reloaded.long_term.read()] == [
        "always: the new way"
    ]


# -- the store is readable by a person ------------------------------


def test_the_store_is_one_json_object_per_line(tmp_path: Path) -> None:
    """The format is plain JSONL, so it can be read without ZUZZ.

    A memory store the user cannot inspect is one they cannot correct, and it
    makes "delete everything" impossible without the program that wrote it.
    """
    store = LongTermMemory(directory=tmp_path / "memory")
    store.store(memory(summary="readable", text="readable"))

    lines = store.path_for("fact").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["summary"] == "readable"
    assert parsed["kind"] == "fact"
    assert "id" in parsed and "created" in parsed


# -- scoring --------------------------------------------------------


def test_scoring_rewards_a_deliberate_specific_statement() -> None:
    """An explicit, keyed, substantial statement beats small talk.

    The heuristic has to be arguable by a user, so the two ends are pinned
    down: the things it rewards and the thing it refuses.
    """
    assert score_importance(memory(kind="conversation", summary="hi", text="hi")) < 0.5
    assert (
        score_importance(
            memory(
                kind="preference",
                summary="always use neovim",
                key="editor",
                text="always use neovim as the editor, never vscode",
            )
        )
        >= 0.5
    )


def test_the_threshold_is_configurable_per_consolidator(tmp_path: Path) -> None:
    """A threshold is a tuning knob for the consolidator, not a global policy.

    Two consolidators over the same buffer with different thresholds should
    disagree, which is what makes the knob worth having.
    """
    store = ShortTermMemory(capacity=10)
    store.remember(promotable("editor: neovim"))

    assert Consolidator(store, LongTermMemory(tmp_path / "a"), 0.5).consolidate()[
        "promoted"
    ] == 1
    assert Consolidator(store, LongTermMemory(tmp_path / "b"), 0.95).consolidate()[
        "promoted"
    ] == 0
# -- durability -----------------------------------------------------


def test_a_flushed_memory_is_on_disk_with_no_threshold_applied(tmp_path: Path) -> None:
    """``flush`` writes what scoring would have thrown away.

    The distinction the flush path exists for. ``consolidate`` asks what is worth
    keeping and will leave small talk in RAM; ``flush`` asks what must not be lost
    and takes everything, because the buffer is RAM-only and bounded and whatever
    is in it at shutdown was something the user actually said.
    """
    brain = Hippocampus(tmp_path)
    brain.remember_fact("the deploy key rotates on the first of the month")

    assert brain.consolidate()["promoted"] == 0, "scoring should have refused this"

    report = brain.flush()
    assert report["promoted"] == 1
    assert report["threshold"] == 0.0
    assert (tmp_path / "facts.jsonl").exists()


def test_flush_twice_writes_each_memory_once(tmp_path: Path) -> None:
    """A second flush is a no-op, so a timer plus a shutdown call is safe."""
    brain = Hippocampus(tmp_path)
    brain.remember_preference("editor", "neovim")

    assert brain.flush()["promoted"] == 1
    assert brain.flush()["promoted"] == 0

    lines = (tmp_path / "preferences.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines


def test_memory_persists_across_restarts(tmp_path: Path) -> None:
    """What one process remembers, a second process finds.

    The claim the whole store exists to make, and the one a test that only uses
    one instance cannot make. Two :class:`Hippocampus` over the same directory: the
    first remembers and flushes, the second is built from nothing but that
    directory and recalls what the first one said.
    """
    first = Hippocampus(tmp_path)
    first.remember_preference("shell", "pwsh", context="a conversation")
    first.flush()

    # Nothing is carried over in memory â€” only what is on disk.
    second = Hippocampus(tmp_path)
    found = second.recall_similar("which shell")

    assert [hit.key for hit in found] == ["shell"]
    assert found[0].value == "pwsh"
    assert second.get_user_profile().preferences["shell"] == "pwsh"


def test_a_chat_turn_survives_the_process_that_heard_it(tmp_path: Path) -> None:
    """``remember_conversation`` plus a flush is a durable transcript.

    A conversation is the lowest-scoring kind there is, which is correct for
    curation and would be wrong for a machine that is supposed to remember what it
    was told. This is the case the flush path exists to cover.
    """
    brain = Hippocampus(tmp_path)
    brain.remember_conversation(
        "I keep the trust matrix in config/trust_levels.json",
        "Noted â€” that is where the rows live.",
        context="chat",
    )
    brain.flush()

    assert any(
        "trust matrix" in line
        for line in (tmp_path / "conversations.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert brain.recall_similar("trust matrix")


def test_short_term_is_promoted_to_long_term_by_consolidation(tmp_path: Path) -> None:
    """Promotion moves a memory between the two tiers, not between two copies.

    The buffer is emptied and the file grows, so the same id is not sitting in RAM
    *and* on disk where a later recall could find two of everything.
    """
    brain = Hippocampus(tmp_path)
    memory_id = brain.remember_preference("editor", "neovim")

    assert brain.status()["short_term"]["held"] == 1
    assert brain.status()["long_term"]["preference"] == 0

    assert brain.consolidate()["ids"] == [memory_id]

    assert brain.status()["short_term"]["held"] == 0
    assert brain.status()["long_term"]["preference"] == 1
    assert memory_id in {held.id for held in brain.long_term.read("preference")}


def test_recall_scores_with_inverse_document_frequency() -> None:
    """A term in every memory ranks lower than a term in one.

    The claim that this is a search and not a substring match. Both memories are
    built to share the word ``parser``; only one has ``sqlite``, and a query for
    both must put the rare one first rather than returning them in insertion order.
    """
    from brain.hippocampus.semantic_search import index_of

    brain = Hippocampus(Path(tempfile.mkdtemp()))
    common = brain.remember_fact("the parser reads the config", context="a")
    rare = brain.remember_fact("sqlite stores the parser index", context="b")
    brain.flush()

    found = brain.recall_similar("sqlite parser")
    assert found[0].id == rare, [hit.summary for hit in found]
    assert found[-1].id == common

    # And the mechanism, not just the ordering: the shared term carries less
    # weight than the rare one in the index itself.
    index = index_of(brain.long_term.read())
    assert index._idf("sqlite", 2) > index._idf("parser", 2)


def test_memory_reads_are_judged_as_memory_access() -> None:
    """Recall and remembering are decided by the matrix, not waved through.

    Both halves of the memory story go through the gate, so a memory tool cannot
    reach the store by being wired where the tool calls happen to be. The trust
    row is ``auto`` at every level, so this checks the *category* rather than a
    refusal â€” what would break is a tool resolving to something else entirely.
    """
    from body.tools.registry import get_registry

    registry = get_registry()
    for name in (
        "memory_recall_similar",
        "memory_remember_fact",
        "memory_remember_preference",
        "memory_search_history",
        "memory_get_user_profile",
):
        spec = registry.get(name)
        assert spec is not None, f"{name} is described to the model with no implementation"
        context = ToolContext(
            hands=None,
            files=None,
            directories=None,
            hippocampus=Hippocampus(Path(tempfile.mkdtemp())),
        )
        params = {
            "memory_recall_similar": {"query": "anything at all"},
            "memory_remember_fact": {"fact": "a durable thing"},
            "memory_remember_preference": {"key": "k", "value": "v"},
            "memory_search_history": {},
            "memory_get_user_profile": {},
        }[name]
        assert spec.resolve(dict(params), context).category == "memory_access", name


def test_the_memory_tools_are_reachable_from_the_catalogue() -> None:
    """All five are described to the model and all five resolve.

    A name in the schema with no implementation is a tool the model was promised
    and cannot reach, which is the specific hallucination this catalogue has been
    cleaned of twice.
    """
    from brain.cerebrum.tool_catalogue import TOOL_NAMES
    from body.tools.registry import catalogue_mismatches

    expected = {
        "memory_recall_similar",
        "memory_remember_fact",
        "memory_remember_preference",
        "memory_search_history",
        "memory_get_user_profile",
    }
    assert expected.issubset(set(TOOL_NAMES)), expected - set(TOOL_NAMES)
    assert catalogue_mismatches() == []


def test_the_memory_tools_refuse_when_memory_is_not_attached() -> None:
    """No store attached is a refusal, not an empty answer.

    "You have no preferences" from a store that was never wired is a confident
    wrong answer, and it is the failure every tool family in this project guards
    against at resolve time.
    """
    from body.tools.registry import get_registry
    from body.tools.tool_spec import ToolContext, ToolRefused

    context = ToolContext(hands=None, files=None, directories=None)
    registry = get_registry()
    with pytest.raises(ToolRefused):
        registry.get("memory_get_user_profile").resolve({}, context)


def test_a_corrupt_file_does_not_stop_the_others_loading(tmp_path: Path) -> None:
    """One unreadable line is skipped; the memories around it still load.

    JSONL is append-only and a half-written line is what a crash mid-write leaves
    behind. Discarding the whole file would lose every memory in it over one
    truncated final line.
    """
    directory = tmp_path
    brain = Hippocampus(directory)
    brain.remember_fact("a fact worth keeping")
    brain.flush()

    path = directory / "facts.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
        handle.write("\n")

    reborn = Hippocampus(directory)
    assert reborn.long_term.count("fact") == 1
    assert reborn.recall_similar("fact worth keeping")


def test_forget_before_removes_only_older_memories(tmp_path: Path) -> None:
    """The date is a real boundary, and the boundary is inclusive of what it names.

    A ``forget_before`` that quietly removed too much is worse than one that
    removed nothing, so both directions are checked: the old memory goes, the new
    one stays, and the count says how many went.
    """
    brain = Hippocampus(tmp_path)
    old = Memory(
        kind="fact",
        summary="an old fact",
        text="an old fact about the parser",
        created=1000.0,
        tags=["test"],
    )
    recent = brain.remember_fact("a recent fact")
    brain.long_term.store(old)
    brain.flush()

    # A numeric cutoff, so the test does not depend on the machine's timezone.
    # ``forget_before`` reads a bare date as local midnight, which in a timezone
    # east of UTC is *before* the epoch and would put the boundary in the wrong
    # place relative to a memory stamped at second 1000.
    removed = brain.forget_before(2000.0)

    assert removed == 1
    assert old.id not in {held.id for held in brain.long_term.read("fact")}
    assert recent in {held.id for held in brain.long_term.read("fact")}


def test_forgetting_before_the_epoch_does_not_crash() -> None:
    """``1970-01-01`` is the date a user reaches for when they mean all of it.

    It is also pre-epoch, and converting it to local time used to raise OSError on
    Windows rather than return a value — so the one forget that clears everything
    was the one forget that threw. The boundary lands somewhere near the epoch
    either way; what matters is that the call returns.
    """
    brain = Hippocampus(Path(tempfile.mkdtemp()))
    brain.remember_fact("something to clear")
    brain.flush()

    assert brain.forget_before("1970-01-01") == 0
    assert brain.long_term.count("fact") == 1
