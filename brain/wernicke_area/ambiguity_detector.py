"""Ambiguity detector for clarification requests."""

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AmbiguityType(Enum):
    """Types of ambiguity."""
    REFERENCE = "reference"
    ACTION = "action"
    TARGET = "target"
    PARAMETER = "parameter"
    SCOPE = "scope"


@dataclass
class Ambiguity:
    """Detected ambiguity."""
    type: AmbiguityType
    description: str
    candidates: List[Any]
    confidence: float
    suggestion: str


class AmbiguityDetector:
    """Detects ambiguities in parsed intents."""

    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        self._known_applications = {
            "chrome", "firefox", "safari", "edge", "browser",
            "vscode", "code", "editor", "terminal", "cmd", "powershell",
            "word", "excel", "powerpoint", "outlook",
            "spotify", "vlc", "discord", "slack", "teams",
        }

    def detect(self, intent, context: Optional[Dict] = None) -> List[Ambiguity]:
        """Detect ambiguities in intent."""
        ambiguities = []

        if intent.confidence < self.confidence_threshold:
            ambiguities.append(Ambiguity(
                type=AmbiguityType.ACTION,
                description=f"Low confidence intent: {intent.type.value}",
                candidates=[],
                confidence=intent.confidence,
                suggestion="Could you clarify what you'd like me to do?",
            ))

        if intent.type.value == "command":
            ambiguities.extend(self._detect_command_ambiguities(intent, context))

        if intent.type.value == "query":
            ambiguities.extend(self._detect_query_ambiguities(intent, context))

        return ambiguities

    def _detect_command_ambiguities(self, intent, context: Optional[Dict]) -> List[Ambiguity]:
        """Detect command ambiguities."""
        ambiguities = []

        if not intent.target and intent.action in ("open", "close", "click", "type"):
            candidates = self._infer_targets(intent.action, context)
            if len(candidates) > 1:
                ambiguities.append(Ambiguity(
                    type=AmbiguityType.TARGET,
                    description=f"Multiple possible targets for {intent.action}",
                    candidates=candidates,
                    confidence=0.5,
                    suggestion=f"Which {intent.action} target? Options: {', '.join(candidates)}",
                ))
            elif len(candidates) == 1:
                ambiguities.append(Ambiguity(
                    type=AmbiguityType.TARGET,
                    description=f"Implicit target for {intent.action}",
                    candidates=candidates,
                    confidence=0.7,
                    suggestion=f"Did you mean {candidates[0]}?",
                ))
            else:
                ambiguities.append(Ambiguity(
                    type=AmbiguityType.TARGET,
                    description=f"No target specified for {intent.action}",
                    candidates=[],
                    confidence=0.3,
                    suggestion=f"What would you like me to {intent.action}?",
                ))

        if intent.action in ("click", "press") and intent.target:
            if intent.target in ("button", "link", "menu", "icon"):
                ambiguities.append(Ambiguity(
                    type=AmbiguityType.TARGET,
                    description=f"Generic target '{intent.target}'",
                    candidates=["specific button name", "coordinates", "text label"],
                    confidence=0.4,
                    suggestion=f"Which {intent.target}? Please specify.",
                ))

        return ambiguities

    def _detect_query_ambiguities(self, intent, context: Optional[Dict]) -> List[Ambiguity]:
        """Detect query ambiguities."""
        ambiguities = []

        if intent.target:
            words = intent.target.split()
            if len(words) <= 2 and not any(w in self._known_applications for w in words):
                ambiguities.append(Ambiguity(
                    type=AmbiguityType.REFERENCE,
                    description=f"Vague query target: {intent.target}",
                    candidates=["specific topic", "application name", "file name"],
                    confidence=0.5,
                    suggestion=f"Could you be more specific about '{intent.target}'?",
                ))

        return ambiguities

    def _infer_targets(self, action: str, context: Optional[Dict]) -> List[str]:
        """Infer possible targets from context."""
        candidates = []

        if context:
            recent_apps = context.get("recent_applications", [])
            candidates.extend(recent_apps)

            last_target = context.get("last_target")
            if last_target:
                candidates.append(last_target)

        if action == "open":
            candidates.extend(list(self._known_applications))
        elif action == "close":
            candidates.extend(context.get("open_applications", []) if context else [])

        return list(set(candidates))

    def needs_clarification(self, intent, context: Optional[Dict] = None) -> bool:
        """Check if intent needs clarification."""
        ambiguities = self.detect(intent, context)
        return any(a.confidence < 0.6 for a in ambiguities)

    def get_clarification_question(self, intent, context: Optional[Dict] = None) -> Optional[str]:
        """Get clarification question for intent."""
        ambiguities = self.detect(intent, context)
        if not ambiguities:
            return None

        critical = [a for a in ambiguities if a.confidence < 0.5]
        if critical:
            return critical[0].suggestion

        return ambiguities[0].suggestion


class AmbiguityError(Exception):
    """Ambiguity detection error."""
    pass
