"""Indice di testo FTS5: titolo, didascalia, parole chiave, nome file, percorso ed etichette."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def reindex(conn: sqlite3.Connection, photo_id: int) -> None:
    photo = conn.execute("SELECT title, caption, deleted_at FROM photos WHERE id=?", (photo_id,)).fetchone()
    conn.execute("DELETE FROM photos_fts WHERE rowid=?", (photo_id,))
    if photo is None:
        return
    files = conn.execute("SELECT dir, name, original_name FROM files WHERE photo_id=?", (photo_id,)).fetchall()
    names = " ".join(f"{f['name']} {f['original_name']}" for f in files)
    paths = " ".join(sorted({f["dir"].replace("/", " ") for f in files if f["dir"]}))
    keywords = " ".join(
        r["word"] for r in conn.execute(
            "SELECT k.word FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id WHERE pk.photo_id=?", (photo_id,))
    )
    labels = " ".join(
        r["name"] for r in conn.execute(
            "SELECT l.name FROM photos_labels pl JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id=?", (photo_id,))
    )
    conn.execute(
        "INSERT INTO photos_fts(rowid, title, caption, keywords, name, path, labels) VALUES (?,?,?,?,?,?,?)",
        (photo_id, photo["title"], photo["caption"], keywords, names, paths, labels),
    )


def reindex_many(conn: sqlite3.Connection, photo_ids: Iterable[int]) -> None:
    for photo_id in photo_ids:
        reindex(conn, photo_id)


def escape(term: str) -> str:
    """Rende sicuro un termine per MATCH: tra virgolette, con prefisso per l'autocompletamento."""
    cleaned = term.replace('"', " ").strip()
    return f'"{cleaned}"*' if cleaned else ""
