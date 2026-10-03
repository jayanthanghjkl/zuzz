"""Prefrontal Cortex — Rule Mapping.

Which immutable rule applies to which action.

Separated from :mod:`brain.prefrontal_cortex.permission_gate` because this is a
lookup table over rules, while the gate is a decision procedure. The split also
makes the block-versus-confirm distinction explicit and testable on its own.
"""

from __future__ import annotations

from typing import Optional

from brain.prefrontal_cortex.action import Action, ActionCategory
from core.paths import PROTECTED, classify

#: Rules that forbid an action outright. No confirmation can unlock these.
BLOCKING_RULES = {
    ActionCategory.DELETE_OS_FILES: "R1",
    ActionCategory.SHARE_DATA: "R2",
    ActionCategory.DISABLE_SAFETY: "R3",
}

#: Rules that forbid *silent* access. The matrix already makes these "ask at
#: every level"; blocking them would make the confirmation dialog unreachable and
#: would overstate what the rules say.
CONFIRMING_RULES = {
    ActionCategory.ACCESS_CREDENTIALS: "R5",
    ActionCategory.INSTALL_SOFTWARE: "R7",
    ActionCategory.RUN_ADMIN: "R4",
}

#: Categories whose target path is checked against the protected list.
PATH_CHECKED = (ActionCategory.DELETE_FILES, ActionCategory.WRITE_FILES)


def blocking_rule(action: Action) -> Optional[str]:
    """Return the immutable rule that forbids this action outright.

    Args:
        action: The proposed action.

    Returns:
        The rule id, or None when no rule forbids the action outright.
    """
    direct = BLOCKING_RULES.get(action.category)
    if direct:
        return direct
    if action.target and action.category in PATH_CHECKED:
        return "R1" if classify(action.target) == PROTECTED else None
    return None


def confirming_rule(action: Action) -> Optional[str]:
    """Return the rule that forces confirmation at every trust level.

    Args:
        action: The proposed action.

    Returns:
        The rule id, or None.
    """
    return CONFIRMING_RULES.get(action.category)