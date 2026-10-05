"""Basal Ganglia — routine registry.

Where routines live between runs: ``config/routines.json``.

Config rather than ``storage/`` on purpose, and the distinction is the same one
``config/trust_levels.json`` makes. **Config is the operator's, storage is the
machine's.** A routine is a thing the user wrote and will want to edit, diff and
back up; a chat transcript is a thing ZUZZ produced and will never read. Putting
routines in storage would mean they sat beside a cache and a log directory and
got swept by whatever prunes those.

Written atomically through a temp file, because the file is read on every request
to the routines list and a partial write would take out every routine on the
machine at once. Every read tolerates a malformed file by reporting zero routines
rather than raising — a routines feature must never be the reason the dashboard
will not load.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from brain.basal_ganglia.context_triggers import Trigger, parse
from brain.basal_ganglia.exceptions import RoutineNameInvalid
from brain.basal_ganglia.routine_builder import Routine, RoutineStep, validate_name

__all__ = ["RoutineRegistry", "routine_from_dict"]


def default_path() -> Path:
    """Return where routines are stored, resolved at call time.

    Resolved lazily rather than at import because ``core.config`` imports nothing
    from this package, and importing it here to build a module-level constant
    would create the cycle the layering avoids — while a constant computed from
    the current directory would point somewhere different depending on where the
    process was started.

    Returns:
        ``<project root>/config/routines.json``.
    """
    from core.config import CONFIG_DIR

    return CONFIG_DIR / "routines.json"


class RoutineRegistry:
    """The stored routines.

    Attributes:
        path: Where the file lives.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Bind to a routines file.

        Args:
            path: The file to read and write. Defaults to
                ``config/routines.json``.
        """
        self.path = Path(path) if path else default_path()

    # -----------------------------------------------------------------
    # Reading
    # -----------------------------------------------------------------

    def load(self) -> List[Routine]:
        """Return every stored routine, in file order.

        Returns:
            The routines. Empty when the file is missing or unreadable, and any
            single unparseable entry is skipped rather than failing the read —
            one bad routine should cost the user that routine, not all of them.
        """
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = data.get("routines", []) if isinstance(data, dict) else data
        routines: List[Routine] = []
        for entry in entries if isinstance(entries, list) else []:
            try:
                routines.append(routine_from_dict(entry))
            except Exception:
                continue
        return routines

    def get(self, name: str) -> Optional[Routine]:
        """Return one routine by name, or None when it is not stored.

        Args:
            name: The routine name.

        Returns:
            The routine, or None.
        """
        wanted = str(name).strip().lower()
        for routine in self.load():
            if routine.name == wanted:
                return routine
        return None

    def names(self) -> List[str]:
        """Return every routine name, in file order."""
        return [routine.name for routine in self.load()]

    # -----------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------

    def save(self, routines: Sequence[Routine]) -> int:
        """Replace the stored routines.

        Args:
            routines: What should be stored.

        Returns:
            How many routines were written.

        Raises:
            RoutineNameInvalid: When two routines share a name. Refused rather
                than resolved, because which one the user meant is not something
                this layer can guess.
            OSError: When the file could not be written.
        """
        seen: Dict[str, int] = {}
        for routine in routines:
            seen[routine.name] = seen.get(routine.name, 0) + 1
        clashes = sorted(name for name, count in seen.items() if count > 1)
        if clashes:
            raise RoutineNameInvalid(
                f"two routines cannot share a name: {', '.join(clashes)}"
            )

        payload = {
            "note": [
                "Routines ZUZZ can run. Config rather than storage on purpose: this",
                "file is the operator's, written and read by people.",
                "",
                "Each routine names EXISTING tools. A routine is executed through the",
                "same resolve -> gate -> execute pipeline as a tool call the model",
                "asked for, and there is no second path into the Body.",
                "",
                "A routine stores no permission. Every step is re-resolved and",
                "re-decided against config/trust_levels.json at the moment the routine",
                "runs, so one created at trust level 5 and run at level 1 does only",
                "what level 1 permits. See the routine_note in trust_levels.json.",
                "",
                "'enabled' is honoured but 'trigger' is advisory: nothing in the shipped",
                "scheduler fires a routine on a trigger yet. A routine can be run by",
                "name, from the Habits page, or by the routine_execute tool."
            ],
            "routines": [routine.to_dict() for routine in routines],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temp.replace(self.path)
        return len(routines)

    def upsert(self, routine: Routine) -> Routine:
        """Store a routine, replacing any routine of the same name.

        Args:
            routine: The routine to store.

        Returns:
            The routine as stored, with its ``updated`` timestamp set.
        """
        stored = Routine(**{**routine.__dict__, "updated": time.time()})
        routines = [item for item in self.load() if item.name != stored.name]
        routines.append(stored)
        self.save(routines)
        return stored

    def delete(self, name: str) -> bool:
        """Remove a routine.

        Args:
            name: The routine to remove.

        Returns:
            True when it was there and is now gone. False when there was nothing
            to remove, which is not an error: deleting twice is a thing users do.
        """
        routines = self.load()
        remaining = [routine for routine in routines if routine.name != str(name).strip().lower()]
        if len(remaining) == len(routines):
            return False
        self.save(remaining)
        return True

    def seed(self) -> int:
        """Add the shipped routines, leaving any the user has changed alone.

        Returns:
            How many were added. A name that already exists is skipped rather
            than overwritten — the user edited it, or deleted it on purpose, and
            either way their file wins.
        """
        from brain.basal_ganglia.routine_builder import seed_routines

        routines = self.load()
        existing = {routine.name for routine in routines}
        added = [routine for routine in seed_routines() if routine.name not in existing]
        if not added:
            return 0
        self.save([*routines, *added])
        return len(added)


def routine_from_dict(entry: Any) -> Routine:
    """Rebuild a routine from stored or API-supplied data.

    Args:
        entry: One routine as plain data.

    Returns:
        The routine.

    Raises:
        RoutineNameInvalid: When the name is missing or unusable.
        Exception: Anything the trigger or step parser raises, so a bad entry is
            skipped by the caller rather than aborting the read.
    """
    if not isinstance(entry, dict):
        raise RoutineNameInvalid("a routine must be a mapping")

    trigger_raw = entry.get("trigger", "manual")
    trigger = trigger_raw if isinstance(trigger_raw, Trigger) else parse(trigger_raw)

    steps: List[RoutineStep] = []
    for step in entry.get("steps", []) or []:
        if not isinstance(step, dict):
            raise RoutineNameInvalid("each step must be a mapping")
        steps.append(
            RoutineStep(
                tool=str(step.get("tool", "")).strip(),
                params=dict(step.get("params", {}) or {}),
                category=str(step.get("category", "")),
                target=str(step.get("target", "")),
                why=str(step.get("why", "")),
            )
        )

    created = float(entry.get("created", 0.0) or 0.0)
    return Routine(
        name=validate_name(entry.get("name", "")),
        description=str(entry.get("description", "")),
        trigger=trigger,
        steps=tuple(steps),
        enabled=bool(entry.get("enabled", False)),
        source=str(entry.get("source", "user")),
        evidence=dict(entry.get("evidence", {}) or {}),
        created=created or time.time(),
        updated=float(entry.get("updated", 0.0) or 0.0) or created or time.time(),
        times_run=int(entry.get("times_run", 0) or 0),
        last_run=float(entry.get("last_run", 0.0) or 0.0),
    )