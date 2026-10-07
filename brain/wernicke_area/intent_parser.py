"""Intent parser for deep language understanding."""

import re
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class IntentType(Enum):
    """Types of intents."""
    COMMAND = "command"
    QUERY = "query"
    CONFIRMATION = "confirmation"
    NEGATION = "negation"
    CLARIFICATION = "clarification"
    GREETING = "greeting"
    FAREWELL = "farewell"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """Parsed intent."""
    type: IntentType
    action: Optional[str] = None
    target: Optional[str] = None
    parameters: Dict[str, Any] = None
    confidence: float = 0.0
    raw_text: str = ""

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


class IntentParser:
    """Parses natural language into structured intents."""

    def __init__(self):
        self._patterns = self._build_patterns()
        self._entities = self._build_entities()

    def _build_patterns(self) -> Dict[IntentType, List[Dict]]:
        """Build intent recognition patterns."""
        return {
            IntentType.COMMAND: [
                {"pattern": r"(?:open|launch|start|run)\s+(.+)", "action": "open"},
                {"pattern": r"(?:close|quit|exit|stop)\s+(.+)", "action": "close"},
                {"pattern": r"(?:click|press|tap)\s+(.+)", "action": "click"},
                {"pattern": r"(?:type|write|enter)\s+(.+)", "action": "type"},
                {"pattern": r"(?:scroll|move)\s+(up|down|left|right)", "action": "scroll"},
                {"pattern": r"(?:go to|navigate to|open)\s+(https?://\S+)", "action": "navigate"},
                {"pattern": r"(?:search|find|look for)\s+(.+)", "action": "search"},
                {"pattern": r"(?:screenshot|capture)\s*(?:screen|window)?", "action": "screenshot"},
            ],
            IntentType.QUERY: [
                {"pattern": r"(?:what|who|where|when|why|how)\s+(.+)", "action": "question"},
                {"pattern": r"(?:tell me|show me|give me)\s+(.+)", "action": "request"},
                {"pattern": r"(?:is|are|can|could|would|should)\s+(.+)", "action": "verify"},
            ],
            IntentType.CONFIRMATION: [
                {"pattern": r"^(?:yes|yeah|yep|sure|okay|ok|confirm|do it)\.?$", "action": "confirm"},
            ],
            IntentType.NEGATION: [
                {"pattern": r"^(?:no|nope|nah|cancel|stop|don't|dont)\.?$", "action": "deny"},
            ],
            IntentType.GREETING: [
                {"pattern": r"^(?:hello|hi|hey|greetings|good morning|good afternoon)\.?$", "action": "greet"},
            ],
            IntentType.FAREWELL: [
                {"pattern": r"^(?:bye|goodbye|see you|farewell|exit)\.?$", "action": "farewell"},
            ],
        }

    def _build_entities(self) -> Dict[str, List[str]]:
        """Build entity recognition patterns."""
        return {
            "application": [
                "chrome", "firefox", "safari", "edge", "browser",
                "vscode", "code", "editor", "terminal", "cmd", "powershell",
                "word", "excel", "powerpoint", "outlook",
                "spotify", "vlc", "discord", "slack", "teams",
                "settings", "control panel", "task manager",
            ],
            "direction": ["up", "down", "left", "right", "top", "bottom"],
            "key": ["enter", "tab", "escape", "esc", "space", "backspace", "delete"],
            "url": [r"https?://\S+"],
        }

    def parse(self, text: str, context: Optional[Dict] = None) -> Intent:
        """Parse text into intent."""
        text = text.strip().lower()
        if not text:
            return Intent(type=IntentType.UNKNOWN, raw_text=text)

        best_intent = Intent(type=IntentType.UNKNOWN, raw_text=text, confidence=0.0)

        for intent_type, patterns in self._patterns.items():
            for pattern_info in patterns:
                pattern = pattern_info["pattern"]
                action = pattern_info.get("action")
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    confidence = 0.8 + (0.2 * len(match.group(0)) / len(text))
                    entities = self._extract_entities(match.group(0))

                    intent = Intent(
                        type=intent_type,
                        action=action,
                        target=match.group(1) if match.groups() else None,
                        parameters={"entities": entities, "match": match.group(0)},
                        confidence=min(confidence, 1.0),
                        raw_text=text,
                    )

                    if intent.confidence > best_intent.confidence:
                        best_intent = intent

        if context:
            best_intent = self._apply_context(best_intent, context)

        logger.debug(f"Parsed intent: {best_intent.type.value} ({best_intent.confidence:.2f})")
        return best_intent

    def _extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extract entities from matched text."""
        entities = {}
        for entity_type, patterns in self._entities.items():
            matches = []
            for pattern in patterns:
                if pattern.startswith(r"http"):
                    found = re.findall(pattern, text)
                else:
                    found = re.findall(rf"\b{re.escape(pattern)}\b", text, re.IGNORECASE)
                matches.extend(found)
            if matches:
                entities[entity_type] = list(set(matches))
        return entities

    def _apply_context(self, intent: Intent, context: Dict) -> Intent:
        """Apply context to refine intent."""
        if intent.type == IntentType.CONFIRMATION and context.get("last_action"):
            intent.parameters["context_action"] = context["last_action"]
            intent.confidence = min(intent.confidence + 0.1, 1.0)

        if intent.type == IntentType.COMMAND and not intent.target and context.get("last_target"):
            intent.target = context["last_target"]
            intent.parameters["context_target"] = context["last_target"]
            intent.confidence = min(intent.confidence + 0.15, 1.0)

        return intent

    def parse_multiple(self, texts: List[str], context: Optional[Dict] = None) -> List[Intent]:
        """Parse multiple texts."""
        return [self.parse(text, context) for text in texts]


class IntentParseError(Exception):
    """Intent parsing error."""
    pass
