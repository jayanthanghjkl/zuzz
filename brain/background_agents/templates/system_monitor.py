"""Background Agent Template: System Monitor.

Watches your laptop/websites, alerts on downtime, logs performance.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


class SystemMonitorAgent:
    """Monitor websites and system health."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._urls = config.get("urls", [])
        self._interval = config.get("check_interval_seconds", 60)
        self._timeout = config.get("timeout_seconds", 10)

    def check_websites(self) -> List[Dict[str, Any]]:
        """Check configured websites for uptime.
        
        Returns:
            List of check results.
        """
        results = []
        for url in self._urls:
            start = time.time()
            try:
                resp = requests.get(url, timeout=self._timeout)
                elapsed = time.time() - start
                results.append({
                    "url": url,
                    "status": resp.status_code,
                    "elapsed_seconds": round(elapsed, 3),
                    "up": resp.status_code == 200,
                })
            except Exception as exc:
                results.append({
                    "url": url,
                    "status": 0,
                    "elapsed_seconds": 0,
                    "up": False,
                    "error": str(exc),
                })
        return results

    def run(self) -> Dict[str, Any]:
        """Run the monitoring cycle."""
        checks = self.check_websites()
        down = [c for c in checks if not c.get("up")]
        return {
            "agent_type": "system_monitor",
            "timestamp": datetime.utcnow().isoformat(),
            "checked": len(checks),
            "down": len(down),
            "checks": checks,
        }
