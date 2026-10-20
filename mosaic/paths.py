"""Percorsi fissi del progetto (relativi alla cartella di installazione, mai hardcoded su un disco)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("MOSAIC_HOME", Path(__file__).resolve().parent.parent))
RUNTIME_DIR = ROOT / "runtime"
MODELS_DIR = ROOT / "models"
FRONTEND_DIST = ROOT / "frontend" / "dist"
DEFAULT_DATA_DIR = ROOT / "data"


def tool(name: str) -> Path | None:
    """Trova un eseguibile portabile in runtime/ oppure nel PATH di sistema."""
    exe = f"{name}.exe" if os.name == "nt" else name
    for candidate in RUNTIME_DIR.rglob(exe) if RUNTIME_DIR.exists() else []:
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None
