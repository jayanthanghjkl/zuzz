"""Communication Hub - track user responses."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


class ResponseTracker:
    """Track pending user responses with timeouts."""

    def __init__(self) -> None:
        self._pending: Dict[str, Dict[str, Any]] = {}

    def register(self, request_id: str, timeout_seconds: int = 300) -> None:
        """Register a pending request.
        
        Args:
            request_id: Unique request identifier.
            timeout_seconds: How long to wait before expiry.
        """
        self._pending[request_id] = {
            "created": time.time(),
            "timeout": timeout_seconds,
            "responded": False,
            "response": None,
        }

    def respond(self, request_id: str, response: Any) -> bool:
        """Record a response.
        
        Args:
            request_id: The request identifier.
            response: The user's response.
        
        Returns:
            True if the request was pending and now marked responded.
        """
        pending = self._pending.get(request_id)
        if pending and not pending["responded"]:
            pending["responded"] = True
            pending["response"] = response
            return True
        return False

    def is_expired(self, request_id: str) -> bool:
        """Check if a pending request has timed out.
        
        Args:
            request_id: The request identifier.
        
        Returns:
            True if expired or not found.
        """
        pending = self._pending.get(request_id)
        if not pending:
            return True
        if pending["responded"]:
            return True
        return (time.time() - pending["created"]) > pending["timeout"]

    def get_response(self, request_id: str) -> Optional[Any]:
        """Get the response for a request, if any.
        
        Args:
            request_id: The request identifier.
        
        Returns:
            The response, or None.
        """
        pending = self._pending.get(request_id)
        if pending and pending["responded"]:
            return pending["response"]
        return None

    def cleanup_expired(self) -> None:
        """Remove expired pending requests."""
        now = time.time()
        expired = [
            rid for rid, p in self._pending.items()
            if p["responded"] or (now - p["created"]) > p["timeout"]
        ]
        for rid in expired:
            del self._pending[rid]
