"""Importa il catalogo curato a mano del vecchio Mosaic (padova_moto_days.db), abbinando le foto per SHA-256."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mosaic.context import Context
from mosaic.meta import sources
from mosaic.organize import albums, tags
from mosaic.search import fts

REQUIRED = {"sha256", "original_name"}


class LegacyError(RuntimeError):
    pass


@dataclass
class LegacyStats:
    total: int = 0
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)
    album: str = ""


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise LegacyError(f"database non trovato: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _words(value: str | None) -> list[str]:
    return [w.strip() for w in re.split(r"[,;]", value or "") if w.strip()]


def import_legacy(ctx: Context, db_path: Path, album_title: str = "Padova Moto Days 2026") -> LegacyStats:
    """Copia descrizione, tag, marca, modello, categoria e inquadratura come dati MANUALI (priorità 64)."""
    legacy = _open_readonly(Path(db_path))
    try:
        columns = {r["name"] for r in legacy.execute("PRAGMA table_info(photos)")}
        if not columns >= REQUIRED:
            raise LegacyError("non è un database del vecchio Mosaic: manca la tabella 'photos' con sha256")
        rows = legacy.execute("SELECT * FROM photos").fetchall()
    finally:
        legacy.close()
    stats = LegacyStats(total=len(rows), album=album_title)
    with ctx.db.tx() as tx:
        album = tx.execute("SELECT * FROM albums WHERE type='album' AND lower(title)=lower(?)", (album_title,)).fetchone() \
            or albums.create_album(tx, album_title, description="Catalogo importato dal vecchio Mosaic")
        for row in rows:
            file_row = tx.execute("SELECT photo_id FROM files WHERE sha256=? AND media_type!='sidecar' LIMIT 1",
                                  (row["sha256"],)).fetchone()
            if file_row is None:
                stats.unmatched.append(row["original_name"])
                continue
            pid = file_row["photo_id"]
            brand, model = (row["brand"] or "").strip(), (row["model"] or "").strip()
            title = " ".join(p for p in (brand, model) if p)
            sources.set_text(tx, pid, "title", title, sources.MANUAL)
            sources.set_text(tx, pid, "caption", row["description"] or "", sources.MANUAL)
            tags.add_keywords(tx, pid, _words(row["tags"]), sources.MANUAL)
            for name, category in ((row["category"], "Categoria"), (brand, "Marca"), (model, "Modello"),
                                   (row["shot_type"], "Inquadratura")):
                if name and str(name).strip():
                    lid = tags.get_or_create_label(tx, str(name).strip(), category)
                    tags.set_photo_label(tx, pid, lid, 0, sources.MANUAL)
            tx.execute("INSERT OR IGNORE INTO photos_albums(album_id, photo_id, position, added_at) VALUES (?,?,?,datetime('now'))",
                       (album["id"], pid, dict(row).get("id", 0)))
            fts.reindex(tx, pid)
            stats.matched += 1
        albums.update_count(tx, album["id"])
    return stats
