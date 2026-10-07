"""External HDD setup for ZUZZ.

Detects external drives, offers migration of models/memory/workspace,
and switches storage paths when the drive is present.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.config import ROOT


def detect_external_drives() -> list[Path]:
    """Return mounted removable drives."""
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        p = Path(f"{letter}:/")
        if p.is_dir():
            try:
                if (p / "System Volume Information").is_dir():
                    continue
            except PermissionError:
                pass
            drives.append(p)
    return drives


def prompt_migration(drive: Path) -> None:
    print(f"External drive detected: {drive}")
    print("Options:")
    print("1. Move Ollama models here")
    print("2. Store memory backups here")
    print("3. Use as workspace for large files")
    choice = input("Select (1-3, or skip): ").strip()
    config_path = ROOT / "config" / "storage.json"
    config = {}
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    if choice == "1":
        config["ollama_models"] = str(drive / "ollama")
    elif choice == "2":
        config["memory_backup"] = str(drive / "zuzz_memory_backup")
    elif choice == "3":
        config["workspace"] = str(drive / "zuzz_workspace")
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Storage config updated at {config_path}")


def migrate_models(target: Path) -> None:
    src = Path.home() / ".ollama" / "models"
    dst = target / "ollama" / "models"
    if src.exists() and not dst.exists():
        print(f"Moving models from {src} to {dst} ...")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print("Models moved.")


def main() -> None:
    drives = detect_external_drives()
    if not drives:
        print("No external drives detected.")
        return
    for drive in drives:
        prompt_migration(drive)


if __name__ == "__main__":
    main()
