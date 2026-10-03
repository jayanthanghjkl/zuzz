"""Brainstem — Activity Logger.

Append-only JSONL log of every action ZUZZ takes. There is deliberately no
update or delete API here: the log is the audit trail the safety system
depends on, and nothing in the agent is allowed to rewrite it.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import ROOT


class ActivityLogger:
    """Writes immutable action records to a JSONL file.

    Attributes:
        path: Absolute path of the JSONL log file.
        _lock: Guards writes so concurrent async tasks do not interleave lines.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Initialise the logger and make sure its directory exists.

        Args:
            path: Log file path. Defaults to ``storage/logs/activity_log.jsonl``.
        """
        self.path = Path(path) if path else ROOT / "storage" / "logs" / "activity_log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, region: str, action: str, **fields: Any) -> Dict[str, Any]:
        """Append one action record and return it.

        Args:
            region: Brain region responsible, e.g. ``"thalamus"``.
            action: Short verb phrase, e.g. ``"route"``.
            **fields: Any extra structured detail.

        Returns:
            The record that was written.
        """
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "region": region,
            "action": action,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def tail(self, count: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent records, oldest first.

        Args:
            count: Maximum number of records to return.

        Returns:
            Up to ``count`` parsed records. Malformed lines are skipped.
        """
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        records: List[Dict[str, Any]] = []
        for raw in lines[-count:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return records


_logger: Optional[ActivityLogger] = None


def get_logger() -> ActivityLogger:
    """Return the process-wide activity logger singleton."""
    global _logger
    if _logger is None:
        _logger = ActivityLogger()
    return _logger
