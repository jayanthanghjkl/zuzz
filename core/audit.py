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

# Added by Session 11 for the Occipital Lobe. A captured screen whose contents
# were classified ``sensitive`` — a password field, an API key, a card number —
# either refused storage or stored after an explicit acknowledgement. Recorded
# both ways, and deliberately without the recognised secret itself: the record
# has to say that a credential was on screen, not write the credential into the
# one file an operator is guaranteed to read while investigating.
SENSITIVE_SCREEN = "sensitive_screen"

# The vision model was not loaded because the Hypothalamus said it would not
# fit. Distinct from a plain model-load refusal because the resource layer
# reached the answer on its own, with no permission decision involved: recording
# it as ``blocked`` would attribute to the gate a decision the gate never made.
VISUAL_MODEL_REFUSED = "visual_model_refused"

# A stored screenshot was handed back over the API. Separate from
# ``sensitive_screen``, which records what was *in* a frame when it was captured:
# this records who afterwards asked for the frame itself, which is the access an
# operator needs when asking "who read that screenshot?". The file name is the
# target, because the name is what identifies the frame without copying its
# contents into the log.
SCREENSHOT_READ = "screenshot_read"

# Added by Phase 3 Step 2 for Body/Legs. The browser is the first part of ZUZZ that
# lets a *third party* choose what ZUZZ reads and does, so the interesting events
# are no longer only about ZUZZ's own decisions — they are about the allowlist, the
# one piece of state a user edits that widens what can happen next:
#
# * ``allowlist_changed`` — a domain was added or forgotten. Recorded both ways,
#   because the list of what ZUZZ will no longer ask about is the security posture
#   of the whole browser and an operator has to be able to reconstruct it.
# * ``url_blocked`` — a URL the guard refuses, recorded whether or not the gate was
#   ever consulted. This is the exfiltration probe: a page reaching for the LAN or
#   the instance-metadata endpoint is refused by the Body, and refusing it quietly
#   would leave the one attempt worth knowing about invisible.
# * ``download_quarantined`` — bytes landed in the holding pen. Recorded with the
#   verdict, not the contents, for the reason ``sensitive_screen`` sets out.
# * ``tab_limit_refused`` — the tab cap stopped a runaway. A bounded browser that
#   quietly truncated work would be worse than one that refuses, so the refusal is
#   the thing worth counting.
ALLOWLIST_CHANGED = "allowlist_changed"
URL_BLOCKED = "url_blocked"
DOWNLOAD_QUARANTINED = "download_quarantined"
TAB_LIMIT_REFUSED = "tab_limit_refused"

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
        SENSITIVE_SCREEN,
        VISUAL_MODEL_REFUSED,
        SCREENSHOT_READ,
        ALLOWLIST_CHANGED,
        URL_BLOCKED,
        DOWNLOAD_QUARANTINED,
        TAB_LIMIT_REFUSED,
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