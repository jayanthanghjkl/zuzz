"""Face API — plugin routes.

Lists plugins in the registry. Approval and loading are handled in Phase 7;
this only surfaces what exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from brain.brainstem.activity_logger import get_logger
from core.config import ROOT

router = APIRouter()

REGISTRY_PATH = ROOT / "plugins" / "plugin_registry.json"
INSTALLED_DIR = ROOT / "plugins" / "installed"


def _read_registry() -> Dict[str, Any]:
    """Load the plugin registry, tolerating a missing or corrupt file."""
    if not REGISTRY_PATH.is_file():
        return {"installed": [], "version": 1}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt plugin registry: {exc}") from exc


@router.get("/api/plugins")
async def list_plugins() -> Dict[str, Any]:
    """Return installed plugins and any unlisted files on disk.

    Returns:
        Registry contents plus a scan of ``plugins/installed``.
    """
    registry = _read_registry()
    files = sorted(p.name for p in INSTALLED_DIR.glob("*.py")) if INSTALLED_DIR.is_dir() else []
    return {
        "installed": registry.get("installed", []),
        "version": registry.get("version", 1),
        "files_on_disk": files,
        "engine_enabled": False,
    }


@router.post("/api/plugins/scan")
async def scan_plugins() -> Dict[str, Any]:
    """Report which plugin files exist but are not yet in the registry.

    Returns:
        A list of unregistered plugin file names.
    """
    registry = _read_registry()
    known = {entry.get("name") for entry in registry.get("installed", []) if isinstance(entry, dict)}
    files = sorted(p.stem for p in INSTALLED_DIR.glob("*.py")) if INSTALLED_DIR.is_dir() else []
    unregistered = [name for name in files if name not in known]
    get_logger().log("face", "plugin_scan", unregistered=unregistered)
    return {"unregistered": unregistered}
