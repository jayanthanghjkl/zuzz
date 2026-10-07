"""Context resolver for resolving references."""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class ContextEntity:
    """Entity in context."""
    id: str
    type: str
    value: Any
    metadata: Dict = field(default_factory=dict)
    timestamp: float = 0.0


class ContextResolver:
    """Resolves contextual references like 'it', 'that', 'last one'."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._entities: deque = deque(maxlen=max_history)
        self._entity_index: Dict[str, ContextEntity] = {}
        self._current_topic: Optional[str] = None
        self._last_action: Optional[str] = None
        self._last_target: Optional[str] = None

    def add_entity(self, entity: ContextEntity) -> None:
        """Add entity to context."""
        self._entities.append(entity)
        self._entity_index[entity.id] = entity

        if entity.type == "action":
            self._last_action = entity.value
        elif entity.type == "target":
            self._last_target = entity.value

    def add_action(self, action: str, target: Optional[str] = None, metadata: Dict = None) -> None:
        """Record an action in context."""
        import time
        entity = ContextEntity(
            id=f"action_{len(self._entities)}",
            type="action",
            value=action,
            metadata=metadata or {},
            timestamp=time.time(),
        )
        self.add_entity(entity)

        if target:
            target_entity = ContextEntity(
                id=f"target_{len(self._entities)}",
                type="target",
                value=target,
                metadata=metadata or {},
                timestamp=time.time(),
            )
            self.add_entity(target_entity)

    def resolve(self, reference: str, context: Optional[Dict] = None) -> Optional[Any]:
        """Resolve a reference like 'it', 'that', 'the last one'."""
        reference = reference.lower().strip()

        if reference in ("it", "that", "this", "the one"):
            return self._resolve_demonstrative()

        if reference in ("the last one", "the previous one", "last one", "previous one"):
            return self._resolve_last()

        if reference.startswith("the ") and reference.endswith(" one"):
            return self._resolve_ordinal(reference)

        if reference in ("him", "her", "them"):
            return self._resolve_pronoun(reference)

        return None

    def _resolve_demonstrative(self) -> Optional[Any]:
        """Resolve 'it', 'that', 'this'."""
        for entity in reversed(self._entities):
            if entity.type in ("target", "object", "application", "file"):
                return entity.value
        return self._last_target

    def _resolve_last(self) -> Optional[Any]:
        """Resolve 'last one', 'previous one'."""
        for entity in reversed(self._entities):
            if entity.type in ("target", "object", "application", "file", "result"):
                return entity.value
        return None

    def _resolve_ordinal(self, reference: str) -> Optional[Any]:
        """Resolve 'the first one', 'the second one', etc."""
        ordinals = {
            "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
            "last": -1, "latest": -1, "most recent": -1,
        }

        for word, index in ordinals.items():
            if word in reference:
                candidates = [e for e in self._entities if e.type in ("target", "object", "result")]
                if candidates:
                    return candidates[index].value
        return None

    def _resolve_pronoun(self, pronoun: str) -> Optional[Any]:
        """Resolve personal pronouns."""
        for entity in reversed(self._entities):
            if entity.type == "person":
                return entity.value
        return None

    def get_last_action(self) -> Optional[str]:
        """Get last action."""
        return self._last_action

    def get_last_target(self) -> Optional[str]:
        """Get last target."""
        return self._last_target

    def get_recent_entities(self, entity_type: Optional[str] = None, limit: int = 10) -> List[ContextEntity]:
        """Get recent entities."""
        entities = list(self._entities)
        if entity_type:
            entities = [e for e in entities if e.type == entity_type]
        return entities[-limit:]

    def set_topic(self, topic: str) -> None:
        """Set current topic."""
        self._current_topic = topic
        logger.debug(f"Topic set to: {topic}")

    def get_topic(self) -> Optional[str]:
        """Get current topic."""
        return self._current_topic

    def clear(self) -> None:
        """Clear context."""
        self._entities.clear()
        self._entity_index.clear()
        self._current_topic = None
        self._last_action = None
        self._last_target = None

    def get_context_summary(self) -> Dict:
        """Get summary of current context."""
        return {
            "topic": self._current_topic,
            "last_action": self._last_action,
            "last_target": self._last_target,
            "entity_count": len(self._entities),
            "recent_targets": [
                e.value for e in self.get_recent_entities("target", 5)
            ],
        }


class ContextError(Exception):
    """Context resolution error."""
    pass
