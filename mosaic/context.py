"""Contesto condiviso: configurazione + database + percorsi di cache."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mosaic import config as config_mod
from mosaic.config import Config
from mosaic.db import Database
from mosaic.paths import MODELS_DIR


@dataclass
class Context:
    cfg: Config
    db: Database
    models_dir: Path = field(default=MODELS_DIR)
    _extras: dict[str, object] = field(default_factory=dict)

    @classmethod
    def open(cls, cfg: Config | None = None, models_dir: Path | None = None) -> "Context":
        cfg = cfg or config_mod.load()
        data = cfg.data_path
        for sub in ("cache/thumbs", "cache/exif", "cache/sidecar", "cache/video", "backup"):
            (data / sub).mkdir(parents=True, exist_ok=True)
        return cls(cfg=cfg, db=Database(data / "mosaic.db"), models_dir=models_dir or MODELS_DIR)

    # --- percorsi -------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self.cfg.data_path

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "cache" / "thumbs"

    @property
    def exif_dir(self) -> Path:
        return self.data_dir / "cache" / "exif"

    @property
    def sidecar_dir(self) -> Path:
        return self.data_dir / "cache" / "sidecar"

    @property
    def video_dir(self) -> Path:
        return self.data_dir / "cache" / "video"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backup"

    # --- servizi condivisi (creati una volta sola, per esempio il processo exiftool) -----
    def service(self, name: str, factory):  # type: ignore[no-untyped-def]
        if name not in self._extras:
            self._extras[name] = factory()
        return self._extras[name]

    def close(self) -> None:
        for svc in self._extras.values():
            closer = getattr(svc, "close", None)
            if callable(closer):
                closer()
        self._extras.clear()
        self.db.close()
