"""Prefrontal Cortex — Trust Matrix.

Reads ``config/trust_levels.json`` and answers what the matrix says for one
action category at one trust level.

Kept apart from :mod:`brain.prefrontal_cortex.permission_gate` so the two
concerns stay separate: this module knows only about permissions as configured,
while the gate knows about immutable rules, path targets and dry runs.

The matrix is re-read on every lookup rather than cached. Editing the JSON is
the intended way to change what ZUZZ may do, and a stale cache would mean the
gate enforces permissions the user no longer believes are in force.
"""

from __future__ import annotations

from typing import Any, Dict

from core.config import get_trust_levels

AUTO = "auto"
ASK = "ask"
BLOCKED = "blocked"

MIN_LEVEL = 1
MAX_LEVEL = 5


def clamp(level: int) -> int:
    """Clamp a trust level into the supported range.

    Args:
        level: Any integer.

    Returns:
        The level, bounded to 1-5.
    """
    return max(MIN_LEVEL, min(MAX_LEVEL, int(level)))


class TrustMatrix:
    """The permission matrix, as configured."""

    def __init__(self) -> None:
        """Load the matrix from disk."""
        self.config: Dict[str, Any] = get_trust_levels()

    def refresh(self) -> None:
        """Re-read the matrix from disk."""
        self.config = get_trust_levels()

    @property
    def actions(self) -> Dict[str, Dict[str, str]]:
        """Return the raw action rows."""
        return self.config.get("actions", {})

    @property
    def policy(self) -> Dict[str, Any]:
        """Return the policy refinements."""
        return self.config.get("policy", {})

    def row_for(self, category: Any) -> Dict[str, str]:
        """Return the per-level decisions for one action category.

        Args:
            category: The action category, or its config key.

        Returns:
            Mapping of level string to decision. Empty when the category is not
            in the matrix, which the gate treats as a refusal.
        """
        return dict(self.actions.get(str(category), {}))

    def is_known(self, category: Any) -> bool:
        """Return True when the matrix has a row for this category.

        Args:
            category: The action category.

        Returns:
            True when the category is configured.
        """
        return str(category) in self.actions

    def decision_for(self, category: Any, level: int) -> str:
        """Return what the matrix says, ignoring the action's target.

        A category blocked at every level stays blocked at every level, so a
        caller cannot reach it by claiming a different trust level.

        Args:
            category: The action category.
            level: Trust level 1-5.

        Returns:
            ``auto``, ``ask``, or ``ask`` for a category the matrix does not
            know — the safe direction to fail.
        """
        row = self.row_for(category)
        if not row:
            return ASK
        if all(entry == BLOCKED for entry in row.values()):
            return BLOCKED
        value = row.get(str(clamp(level)), ASK)
        return BLOCKED if value == BLOCKED else value

    def delete_temp_auto_from_level(self) -> int:
        """Return the level at which temp-file deletion may auto-approve."""
        return int(self.policy.get("delete_temp_auto_from_level", 3))

    def blocks_protected_writes(self) -> bool:
        """Return True when writes to protected paths are blocked outright."""
        return bool(self.policy.get("protected_paths_block_writes", True))

    def default_level(self) -> int:
        """Return the trust level used when none has been set."""
        return int(self.policy.get("default_trust_level", 1))