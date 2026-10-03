"""Brainstem — Server.

Uvicorn launcher for the Face API. `start.py` calls :func:`run`; this module
owns host, port and log level so they can also be overridden by environment.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import env, get_settings


def resolve_bind() -> Dict[str, Any]:
    """Determine the host and port to bind.

    Returns:
        Dict with ``host``, ``port`` and ``log_level`` merged from
        ``config/settings.json`` and the environment.
    """
    settings = get_settings()
    return {
        "host": env("ZUZZ_HOST", str(settings.get("host", "127.0.0.1"))),
        "port": int(env("ZUZZ_PORT", str(settings.get("port", 8080)))),
        "log_level": env("LOG_LEVEL", str(settings.get("log_level", "info"))).lower(),
    }


def run(host: Optional[str] = None, port: Optional[int] = None, reload: bool = False) -> None:
    """Start the ZUZZ web server.

    Args:
        host: Override the bind address.
        port: Override the bind port.
        reload: Enable uvicorn auto-reload for development.

    Raises:
        RuntimeError: If uvicorn is not installed.
    """
    import uvicorn

    bind = resolve_bind()
    bind_host = host or bind["host"]
    bind_port = port or bind["port"]
    print(f"ZUZZ brain online at http://{bind_host}:{bind_port}")
    uvicorn.run(
        "face.api.server:app",
        host=bind_host,
        port=bind_port,
        log_level=bind["log_level"],
        reload=reload,
    )
