"""Safety audit log tests.

The audit log is the record a security review reads, so the properties that
matter are: it only accepts safety events, it is append-only, and it survives
reading back what was written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.audit import (
    BLOCKED,
    EVENT_TYPES,
    FORBIDDEN,
    TRUST_CHANGE,
    AuditLog,
)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    """Return an audit log writing to a temporary file."""
    return AuditLog(path=tmp_path / "logs" / "audit_log.jsonl")


def test_records_a_blocked_action(audit: AuditLog) -> None:
    """A block is recorded with enough detail to understand it later."""
    audit.record(
        BLOCKED,
        "amygdala scored this forbidden",
        category="delete_files",
        target="rm -rf /",
        level="forbidden",
        trust_level=5,
    )
    entries = audit.tail()
    assert len(entries) == 1
    assert entries[0]["event"] == BLOCKED
    assert entries[0]["target"] == "rm -rf /"
    assert entries[0]["trust_level"] == 5
    assert "ts" in entries[0]


def test_extra_fields_are_kept(audit: AuditLog) -> None:
    """Detail specific to an event survives verbatim."""
    audit.record(FORBIDDEN, "vetoed", score=10, matched=["root_wipe"])
    entry = audit.tail()[0]
    assert entry["score"] == 10
    assert entry["matched"] == ["root_wipe"]


def test_ordinary_activity_is_refused(audit: AuditLog) -> None:
    """Chat and routing events belong in the activity log, not here."""
    with pytest.raises(ValueError, match="not a safety event"):
        audit.record("chat", "user said hello")
    assert audit.tail() == []


def test_log_is_append_only(audit: AuditLog) -> None:
    """Every record is a new line; nothing is rewritten."""
    audit.record(BLOCKED, "first")
    audit.record(TRUST_CHANGE, "second")
    lines = audit.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["summary"] for line in lines] == ["first", "second"]


def test_by_event_filters(audit: AuditLog) -> None:
    """A review can read one kind of event without filtering by hand."""
    audit.record(BLOCKED, "one")
    audit.record(TRUST_CHANGE, "two")
    audit.record(BLOCKED, "three")
    blocked = audit.by_event(BLOCKED)
    assert [entry["summary"] for entry in blocked] == ["one", "three"]


def test_missing_file_reads_as_empty(audit: AuditLog) -> None:
    """Reading a log that was never written is not an error."""
    assert audit.tail() == []
    assert audit.by_event(BLOCKED) == []


def test_corrupt_line_is_skipped_not_fatal(audit: AuditLog) -> None:
    """A truncated final line must not hide the records before it."""
    audit.record(BLOCKED, "good record")
    with audit.path.open("a", encoding="utf-8") as handle:
        handle.write("{truncated\n")
    assert [entry["summary"] for entry in audit.tail()] == ["good record"]


def test_event_types_are_the_documented_set() -> None:
    """The vocabulary of safety events is explicit, not open-ended."""
    assert FORBIDDEN in EVENT_TYPES
    assert TRUST_CHANGE in EVENT_TYPES
    assert "chat" not in EVENT_TYPES