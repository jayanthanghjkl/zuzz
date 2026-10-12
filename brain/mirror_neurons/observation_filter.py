"""Mirror Neurons — what to watch and what to ignore.

An observation session records a lot. A user typing a password, moving the
pointer to a close button, double-clicking a document — all of these look the
same on the wire, and the difference between "learning a workflow" and
"learning a secret" is entirely in what the filter decides to keep.

The filter is *explicit* rather than learned. A learned one would need a
training set of "things the user did that were fine" versus "things the user
did that were private", and the only honest source for that is the user
themselves. So the filter is a set of rules the user can read and change from
the Agents page, and every refusal is logged with the rule that fired.

Rules are evaluated in order. The first one that matches wins, so a specific
rule beats a general one and "always ignore" beats "always watch".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "ObservationFilter",
    "FilterRule",
    "FilterVerdict",
    "DEFAULT_FILTER",
    "always_ignore",
    "never_ignore",
]

#: Verdicts the filter can return.
WATCH = "watch"
IGNORE = "ignore"


@dataclass(frozen=True)
class FilterRule:
    """One rule in the observation filter.

    Attributes:
        name: What this rule is, for the UI and the log.
        kind: What part of an act it matches — ``"any"``, ``"tool"``,
            ``"target"``, ``"context"`` or ``"category"``.
        pattern: What to match against. Substring match, case-insensitive.
        verdict: What to do when it matches.
        reason: Why, shown to the user when something is ignored.
    """

    name: str
    kind: str
    pattern: str
    verdict: str
    reason: str = ""


def _match(rule: FilterRule, record: Dict[str, Any]) -> bool:
    """Return whether ``rule`` matches ``record``."""
    if rule.kind == "any":
        haystack = " ".join(str(v) for v in record.values() if v is not None)
    elif rule.kind == "tool":
        haystack = str(record.get("tool", ""))
    elif rule.kind == "target":
        haystack = str(record.get("target", ""))
    elif rule.kind == "context":
        haystack = str(record.get("context", ""))
    elif rule.kind == "category":
        haystack = str(record.get("category", ""))
    else:
        return False
    return rule.pattern.lower() in haystack.lower()


@dataclass
class FilterVerdict:
    """The result of running the filter on one act.

    Attributes:
        watched: Whether the act was recorded.
        rule: The rule that decided, if any.
    """

    watched: bool
    rule: Optional[FilterRule] = None

    @property
    def verdict(self) -> str:
        """Return ``watch`` or ``ignore``."""
        return WATCH if self.watched else IGNORE


class ObservationFilter:
    """Decide whether one recorded act is worth keeping.

    The default filter is permissive — it watches everything — because the
    whole point of the region is to learn, and an empty filter learns nothing.
    The user adds rules to narrow it, and each rule is a sentence they can
    read: "ignore anything that touches a password field", "ignore anything
    in a banking app". The rules are in :data:`DEFAULT_FILTER` so a fresh
    install starts with the safe, readable set.
    """

    def __init__(self, rules: Optional[Sequence[FilterRule]] = None) -> None:
        """Build a filter from a list of rules.

        Args:
            rules: The rules, in priority order. Defaults to the shipped
                set, which watches everything.
        """
        self.rules: List[FilterRule] = list(rules) if rules is not None else list(DEFAULT_FILTER)

    def decide(self, record: Dict[str, Any]) -> FilterVerdict:
        """Run every rule against ``record`` and return the first verdict.

        Args:
            record: One act, as the recorder built it.

        Returns:
            The verdict, with the rule that fired when one did. The default
            is to watch — an act that matches no rule is recorded, because
            the rules are for saying "not this", and silence is not a no.
        """
        for rule in self.rules:
            if _match(rule, record):
                return FilterVerdict(watched=rule.verdict == WATCH, rule=rule)
        return FilterVerdict(watched=True)

    def describe(self) -> List[Dict[str, str]]:
        """Return the rules as plain data, for the Agents page."""
        return [
            {
                "name": rule.name,
                "kind": rule.kind,
                "pattern": rule.pattern,
                "verdict": rule.verdict,
                "reason": rule.reason,
            }
            for rule in self.rules
        ]


def always_ignore(name: str, kind: str, pattern: str, reason: str = "") -> FilterRule:
    """Build a rule that discards whatever it matches."""
    return FilterRule(name=name, kind=kind, pattern=pattern, verdict=IGNORE, reason=reason)


def never_ignore(name: str, kind: str, pattern: str, reason: str = "") -> FilterRule:
    """Build a rule that keeps whatever it matches, overriding an earlier ignore."""
    return FilterRule(name=name, kind=kind, pattern=pattern, verdict=WATCH, reason=reason)


#: The shipped filter. Watches everything — the user narrows it from the UI.
DEFAULT_FILTER: List[FilterRule] = []