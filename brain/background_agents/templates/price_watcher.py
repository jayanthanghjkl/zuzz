"""Background Agent Template: Price Watcher.

Monitors product prices and alerts when price drops.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class PriceWatcherAgent:
    """Monitor product prices and alert on drops."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._items = config.get("items", [])
        self._history_file = config.get("history_file", "storage/price_history.jsonl")
        self._threshold = config.get("drop_threshold_percent", 5.0)

    def check_prices(self) -> List[Dict[str, Any]]:
        """Check current prices for watched items.
        
        Returns:
            List of price check results.
        """
        results = []
        for item in self._items:
            price = self._fetch_price(item.get("url", ""))
            results.append({
                "name": item.get("name", "Unknown"),
                "url": item.get("url", ""),
                "current_price": price,
                "target_price": item.get("target_price"),
            })
        return results

    def _fetch_price(self, url: str) -> float:
        """Fetch price from a URL."""
        # Placeholder: in production use requests + BeautifulSoup or API
        return 0.0

    def detect_drops(self, checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect price drops against history."""
        history = self._load_history()
        drops = []
        for check in checks:
            name = check["name"]
            prev = history.get(name)
            if prev and check["current_price"] < prev * (1 - self._threshold / 100):
                drops.append(check)
        return drops

    def _load_history(self) -> Dict[str, float]:
        history = {}
        if os.path.exists(self._history_file):
            try:
                with open(self._history_file, "r", encoding="utf-8") as fh:
                    for line in fh:
                        rec = json.loads(line)
                        history[rec["name"]] = rec["price"]
            except Exception:
                pass
        return history

    def save_history(self, checks: List[Dict[str, Any]]) -> None:
        """Save price checks to history."""
        os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
        with open(self._history_file, "a", encoding="utf-8") as fh:
            for check in checks:
                fh.write(json.dumps({
                    "name": check["name"],
                    "price": check["current_price"],
                    "ts": datetime.utcnow().isoformat(),
                }) + "\n")

    def run(self) -> Dict[str, Any]:
        """Run the price watcher cycle."""
        checks = self.check_prices()
        drops = self.detect_drops(checks)
        self.save_history(checks)
        return {
            "agent_type": "price_watcher",
            "timestamp": datetime.utcnow().isoformat(),
            "checked": len(checks),
            "drops": len(drops),
            "items": checks,
            "alerts": drops,
        }
