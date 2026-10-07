"""Wernicke's Area - Main orchestrator for language comprehension."""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .intent_parser import IntentParser, Intent, IntentType
from .context_resolver import ContextResolver
from .ambiguity_detector import AmbiguityDetector, Ambiguity
from .exceptions import WernickeAreaError

logger = logging.getLogger(__name__)


@dataclass
class ComprehensionResult:
    """Result of language comprehension."""
    intent: Intent
    resolved_entities: Dict[str, Any]
    ambiguities: List[Ambiguity]
    needs_clarification: bool
    clarification_question: Optional[str]


class WernickeArea:
    """Orchestrator for deep language comprehension."""

    def __init__(self, confidence_threshold: float = 0.7):
        self.intent_parser = IntentParser()
        self.context_resolver = ContextResolver()
        self.ambiguity_detector = AmbiguityDetector(confidence_threshold)
        self._conversation_history: List[Dict] = []

    def comprehend(
        self,
        text: str,
        context: Optional[Dict] = None,
    ) -> ComprehensionResult:
        """Comprehend natural language text."""
        parse_context = self._build_parse_context(context)
        intent = self.intent_parser.parse(text, parse_context)

        resolved = self._resolve_references(intent, parse_context)
        ambiguities = self.ambiguity_detector.detect(intent, parse_context)
        needs_clarification = self.ambiguity_detector.needs_clarification(intent, parse_context)
        clarification = self.ambiguity_detector.get_clarification_question(intent, parse_context)

        result = ComprehensionResult(
            intent=intent,
            resolved_entities=resolved,
            ambiguities=ambiguities,
            needs_clarification=needs_clarification,
            clarification_question=clarification,
        )

        self._update_context(text, intent, result)
        return result

    def _build_parse_context(self, context: Optional[Dict]) -> Dict:
        """Build context for parsing."""
        ctx = self.context_resolver.get_context_summary()
        if context:
            ctx.update(context)
        return ctx

    def _resolve_references(self, intent: Intent, context: Dict) -> Dict:
        """Resolve references in intent."""
        resolved = {}

        if intent.target:
            resolved["target"] = self.context_resolver.resolve(intent.target, context) or intent.target

        if intent.parameters.get("entities"):
            for entity_type, entities in intent.parameters["entities"].items():
                resolved[entity_type] = []
                for entity in entities:
                    resolved_entity = self.context_resolver.resolve(entity, context)
                    resolved[entity_type].append(resolved_entity or entity)

        return resolved

    def _update_context(self, text: str, intent: Intent, result: ComprehensionResult) -> None:
        """Update context with new information."""
        self.context_resolver.add_action(intent.action or "unknown", intent.target)

        if intent.parameters.get("entities"):
            for entity_type, entities in intent.parameters["entities"].items():
                for entity in entities:
                    from .context_resolver import ContextEntity
                    import time
                    self.context_resolver.add_entity(ContextEntity(
                        id=f"{entity_type}_{len(self.context_resolver._entities)}",
                        type=entity_type,
                        value=entity,
                        timestamp=time.time(),
                    ))

        self._conversation_history.append({
            "text": text,
            "intent": intent.type.value,
            "action": intent.action,
            "target": intent.target,
            "timestamp": __import__("time").time(),
        })

        if len(self._conversation_history) > 100:
            self._conversation_history = self._conversation_history[-100:]

    def get_context(self) -> Dict:
        """Get current context."""
        return self.context_resolver.get_context_summary()

    def clear_context(self) -> None:
        """Clear context."""
        self.context_resolver.clear()
        self._conversation_history.clear()

    def get_history(self, limit: int = 10) -> List[Dict]:
        """Get conversation history."""
        return self._conversation_history[-limit:]


class WernickeAreaError(Exception):
    """Wernicke's Area error."""
    pass
