"""Background Agent Template: Research Agent.

Takes a topic + question, searches overnight, delivers morning report.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class ResearchAgent:
    """Overnight research agent."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._topic = config.get("topic", "")
        self._question = config.get("question", "")

    def research(self) -> Dict[str, Any]:
        """Perform research on the configured topic.
        
        Returns:
            Research report dict.
        """
        logger.info("researching: %s - %s", self._topic, self._question)
        # Placeholder: in production, call a search API and summarize
        findings = [
            f"Key finding 1 about {self._topic}",
            f"Key finding 2 about {self._topic}",
        ]
        report = "\n\n".join(findings)
        return {
            "agent_type": "research",
            "topic": self._topic,
            "question": self._question,
            "timestamp": datetime.utcnow().isoformat(),
            "report": report,
        }

    def run(self) -> Dict[str, Any]:
        """Run the research cycle."""
        return self.research()
