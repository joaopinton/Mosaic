"""Chiavi API dei servizi cloud: in data/secrets.yaml (mai in config.yaml) oppure in una variabile d'ambiente."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from mosaic.config import CLOUD_PROVIDERS

FILE_NAME = "secrets.yaml"
ENV_VARS = {"nvidia": "NVIDIA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MAX_KEY_LENGTH = 512

_lock = threading.Lock()


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def _read(data_dir: Path) -> dict[str, str]:
    try:
        raw = yaml.safe_load(_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v.strip() for k, v in raw.items() if k in CLOUD_PROVIDERS and isinstance(v, str) and v.strip()}


def get_key(data_dir: Path, provider: str) -> str | None:
    """La chiave salvata dalle Impostazioni; se manca, quella della variabile d'ambiente."""
    saved = _read(data_dir).get(provider)
    if saved:
        return saved
    return os.environ.get(ENV_VARS.get(provider, ""), "").strip() or None


def key_status(data_dir: Path) -> dict[str, str | None]:
    """Da dove viene la chiave di ogni servizio: "file", "env" o None. La chiave stessa non esce mai."""
    saved = _read(data_dir)
    return {p: "file" if p in saved else "env" if get_key(data_dir, p) else None for p in CLOUD_PROVIDERS}


def set_key(data_dir: Path, provider: str, key: str | None) -> None:
    """Salva la chiave; vuota o None la cancella. Scrittura atomica."""
    if provider not in CLOUD_PROVIDERS:
        raise ValueError(f"servizio sconosciuto: {provider}")
    key = (key or "").strip()
    if len(key) > MAX_KEY_LENGTH or any(c.isspace() or not c.isprintable() for c in key):
        raise ValueError("chiave non valida")
    with _lock:
        keys = _read(data_dir)
        if key:
            keys[provider] = key
        else:
            keys.pop(provider, None)
        path = _path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(keys, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
