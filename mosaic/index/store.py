"""Helper condivisi per leggere file e foto dal database."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from mosaic.db import Database
from mosaic.index.discover import FileEntry
from mosaic.index.related import primary_rank


def new_uid(prefix: str = "p") -> str:
    return prefix + secrets.token_hex(8)


def abs_path(row: sqlite3.Row | dict) -> Path:
    """Percorso assoluto di un record `files` (root + dir + name)."""
    root = Path(row["root"])
    return root / row["dir"] / row["name"] if row["dir"] else root / row["name"]


def photo_files(db: Database, photo_id: int, include_missing: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM files WHERE photo_id=?"
    if not include_missing:
        sql += " AND missing=0"
    return db.all(sql + " ORDER BY is_primary DESC, id", (photo_id,))


def primary_file(db: Database, photo_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT * FROM files WHERE photo_id=? AND missing=0 AND media_type!='sidecar' "
        "ORDER BY is_primary DESC, id LIMIT 1",
        (photo_id,),
    )


def choose_primary(rows: list[sqlite3.Row]) -> int | None:
    """Sceglie il file principale tra i file di una foto (stessa precedenza dell'indicizzazione)."""
    candidates = [r for r in rows if r["media_type"] != "sidecar" and not r["missing"]]
    if not candidates:
        return None

    def rank(row: sqlite3.Row) -> tuple[int, str]:
        entry = FileEntry(row["name"], row["dir"], abs_path(row), row["size"], row["mod_time"])
        return primary_rank(entry)

    return min(candidates, key=rank)["id"]
