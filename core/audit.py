"""Safety audit log.

The activity log records *everything*, which makes it noisy. Blocked actions,
forbidden scores, confirmation prompts, trust level changes and kill switch
events are rare, and they are the records worth reviewing.

They go to `storage/logs/audit_log.jsonl`, append-only, separate from the
activity log so a security review reads one focused file rather than filtering
thousands of chat messages.

Lives in `core/` because three regions write to it: the Prefrontal Cortex
(decisions, trust changes), the Amygdala (threat assessments) and the Spinal Cord
(kill switch). It is append-only for the same reason the activity log is — an
audit trail that can be rewritten is not an audit trail.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import ROOT

DEFAULT_PATH = ROOT / "storage" / "logs" / "audit_log.jsonl"

#: Event types that belong here. Anything else is a bug in the caller — an
#: ordinary chat or routing decision belongs in the activity log, not in the
#: security record.
BLOCKED = "blocked"
FORBIDDEN = "forbidden"
CONFIRMATION_REQUESTED = "confirmation_requested"
CONFIRMATION_GRANTED = "confirmation_granted"
CONFIRMATION_DENIED = "confirmation_denied"
TRUST_CHANGE = "trust_change"
KILL_SWITCH = "kill_switch"
RULE_VIOLATION = "rule_violation"
THREAT_ASSESSED = "threat_assessed"

# Added by Step 6 for the Hypothalamus. Each is a safety event in its own right,
# not a variant of one above:
#
# * ``thermal_escalation`` — the guardian throttled or shut down local inference
#   because the hardware was too hot. Protecting the machine from itself.
# * ``thermal_sensors_lost`` — the temperature could no longer be read, so the
#   protection above silently stopped applying. The most important one here,
#   because nothing else reports it.
# * ``model_protective_unload`` — the system evicted a model with no decision and
#   no request behind it, changing what is loaded without asking.
# * ``model_released`` — shutdown reclaimed every allocation, including any the
#   backend would not give up. Written so a stale reservation is traceable.
THERMAL_ESCALATION = "thermal_escalation"
THERMAL_SENSORS_LOST = "thermal_sensors_lost"
MODEL_PROTECTIVE_UNLOAD = "model_protective_unload"
MODEL_RELEASED = "model_released"

# Added by Session 9 (Priority 3, Defect 2). A refusal that happened *before* the
# gate was ever consulted — the Body's classification guards rejecting a secret
# file, a protected location or an escaping relative path while building the
# prompt. These attempts were previously refused correctly and recorded nowhere, so
# a credential-theft or traversal probe left no trace at all: the single most
# security-relevant class of event was the one class the audit log could not see.
PRECHECK_REFUSAL = "precheck_refusal"

EVENT_TYPES = frozenset(
    {
        BLOCKED,
        FORBIDDEN,
        CONFIRMATION_REQUESTED,
        CONFIRMATION_GRANTED,
        CONFIRMATION_DENIED,
        TRUST_CHANGE,
        KILL_SWITCH,
        RULE_VIOLATION,
        THREAT_ASSESSED,
        THERMAL_ESCALATION,
        THERMAL_SENSORS_LOST,
        MODEL_PROTECTIVE_UNLOAD,
        MODEL_RELEASED,
        PRECHECK_REFUSAL,
    }
)


class AuditLog:
    """Append-only record of safety-critical events.

    Attributes:
        path: The JSONL file events are appended to.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Open the audit log.

        Args:
            path: Override for the log file, used by tests.
        """
        self.path = Path(path) if path else DEFAULT_PATH

    def record(
        self,
        event: str,
        summary: str,
        *,
        category: str = "",
        target: str = "",
        level: str = "",
        rule_id: str = "",
        trust_level: Optional[int] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Append one safety event.

        Args:
            event: One of :data:`EVENT_TYPES`.
            summary: One line describing what happened.
            category: The action category involved.
            target: What the action acted on.
            level: Threat level or decision.
            rule_id: Immutable rule involved, when one was.
            trust_level: Trust level at the time.
            **fields: Extra detail recorded verbatim.

        Returns:
            The record as written.

        Raises:
            ValueError: If the event type is not a safety event.
        """
        if event not in EVENT_TYPES:
            raise ValueError(f"{event!r} is not a safety event; use the activity log")
        entry: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event": event,
            "summary": summary,
            "category": category,
            "target": target,
            "level": level,
            "rule_id": rule_id,
            "trust_level": trust_level,
        }
        entry.update(fields)
        self._append(entry)
        return entry

    def _append(self, entry: Dict[str, Any]) -> None:
        """Write one JSON line, creating the directory if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def tail(self, count: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent events.

        Args:
            count: How many to return.

        Returns:
            The tail of the log, oldest first.
        """
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, min(count, 500)) :]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def by_event(self, event: str, count: int = 50) -> List[Dict[str, Any]]:
        """Return recent events of one type.

        Args:
            event: The event type to filter on.
            count: Maximum entries to return.

        Returns:
            Matching entries, oldest first.
        """
        return [entry for entry in self.tail(count * 4) if entry.get("event") == event][-count:]


_AUDIT: Optional[AuditLog] = None


def get_audit_log() -> AuditLog:
    """Return the process-wide audit log singleton."""
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = AuditLog()
    return _AUDIT