"""Amygdala — Threat Patterns.

Pattern loading, scoring weights and the assessment cache, kept apart from
:mod:`brain.amygdala.threat_detector` so the detector reads as scoring logic
rather than as a mix of configuration and arithmetic.

Two decisions live here:

* **Weights are code, patterns are config.** `config/danger_patterns.json` holds
  the regexes and the categories they apply to; this module holds what each one
  is worth. Editing a pattern cannot silently change its score.
* **A category-aware pattern is what makes the detector trustworthy.** A pattern
  that matched a file's name but does not apply to file actions is recorded as
  skipped rather than counted, so the score can be audited.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, Pattern, Set, Tuple

from core.config import get_danger_patterns

ALL = "all"

#: Score contributions. Kept here rather than in the config so that tuning a
#: regex can never change how much it is worth.
POINTS: Dict[str, int] = {
    "recursive_delete": 5,
    "root_wipe": 5,
    "recursive_delete_windows": 5,
    "system_path": 4,
    "disk_utility": 6,
    "registry_write": 5,
    "power_state": 3,
    "credential_access": 4,
    "force_flag": 2,
    "pipe_to_shell": 5,
    "privilege_escalation": 3,
}

#: Patterns whose presence forces confirmation at *every* trust level,
#: independent of score. Immutable rule R4: never run destructive commands
#: without explicit user confirmation. Without this, a scoped `rm -rf
#: node_modules` scores 5 and would auto-approve at trust level 5 — autonomy
#: over routine work is not permission to delete silently.
FORCE_CONFIRM_PATTERNS: Set[str] = {
    "recursive_delete",
    "root_wipe",
    "recursive_delete_windows",
    "disk_utility",
    "registry_write",
    "privilege_escalation",
    "pipe_to_shell",
    "power_state",
}

#: Bounded cache of assessments. ZUZZ repeats the same commands a lot, and
#: assessment sits in the request path, so a repeat costs a dict lookup.
_ASSESSMENT_CACHE: Dict[Tuple[str, str], object] = {}
_CACHE_LIMIT = 512


@lru_cache(maxsize=1)
def compiled_patterns() -> Tuple[Tuple[str, Pattern[str], Tuple[str, ...]], ...]:
    """Compile the configured patterns once.

    A malformed pattern is skipped rather than allowed to raise: a broken regex
    must not take the safety layer down, and the remaining patterns still apply.

    Returns:
        A tuple of (id, compiled regex, applicable categories).
    """
    out = []
    for entry in get_danger_patterns().get("patterns", []):
        try:
            compiled = re.compile(entry["regex"], re.IGNORECASE)
        except re.error:
            continue
        out.append((entry["id"], compiled, tuple(entry.get("applies_to", [ALL]))))
    return tuple(out)


def applies_to(pattern_categories: Tuple[str, ...], category: str) -> bool:
    """Return True when a pattern is relevant to this action category.

    Args:
        pattern_categories: Categories the pattern declares.
        category: The action category under assessment.

    Returns:
        True when the pattern should be considered.
    """
    return ALL in pattern_categories or category in pattern_categories


def cache_get(key: Tuple[str, str]) -> object | None:
    """Return a cached assessment, or None."""
    return _ASSESSMENT_CACHE.get(key)


def cache_put(key: Tuple[str, str], assessment: object) -> None:
    """Store an assessment, evicting everything if the cache is full."""
    if len(_ASSESSMENT_CACHE) >= _CACHE_LIMIT:
        _ASSESSMENT_CACHE.clear()
    _ASSESSMENT_CACHE[key] = assessment


def clear_cache() -> None:
    """Empty the assessment cache. Used by tests and after a config reload."""
    _ASSESSMENT_CACHE.clear()