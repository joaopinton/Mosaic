"""Etichette e parole chiave con priorità della fonte."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from mosaic.meta.sources import allowed
from mosaic.textutil import slugify


def get_or_create_label(conn: sqlite3.Connection, name: str, category: str = "", priority: int = 0) -> int:
    name = name.strip()
    slug = slugify(name)
    row = conn.execute("SELECT id FROM labels WHERE slug=?", (slug,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO labels(name, slug, category, priority) VALUES (?,?,?,?)", (name, slug, category, priority)
    ).lastrowid


def set_photo_label(conn: sqlite3.Connection, photo_id: int, label_id: int, uncertainty: int, src: int) -> bool:
    """Collega un'etichetta a una foto; una fonte di priorità inferiore non sostituisce una superiore."""
    row = conn.execute("SELECT src FROM photos_labels WHERE photo_id=? AND label_id=?", (photo_id, label_id)).fetchone()
    if row and not allowed(row["src"], src):
        return False
    conn.execute(
        "INSERT INTO photos_labels(photo_id, label_id, uncertainty, src) VALUES (?,?,?,?) "
        "ON CONFLICT(photo_id, label_id) DO UPDATE SET uncertainty=excluded.uncertainty, src=excluded.src",
        (photo_id, label_id, max(0, min(100, uncertainty)), src),
    )
    return True


def clear_labels(conn: sqlite3.Connection, photo_id: int, src: int) -> None:
    """Rimuove le etichette di una data fonte (prima di rianalizzare); quelle manuali restano."""
    conn.execute("DELETE FROM photos_labels WHERE photo_id=? AND src=?", (photo_id, src))


def photo_labels(conn: sqlite3.Connection, photo_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT l.id, l.name, l.slug, l.category, pl.uncertainty, pl.src FROM photos_labels pl "
        "JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id=? ORDER BY pl.uncertainty, l.name",
        (photo_id,),
    ).fetchall()


def add_keywords(conn: sqlite3.Connection, photo_id: int, words: Iterable[str], src: int) -> int:
    added = 0
    for raw in words:
        word = raw.strip().lower()
        if not word or len(word) > 80:
            continue
        row = conn.execute("SELECT id FROM keywords WHERE word=?", (word,)).fetchone()
        kid = row["id"] if row else conn.execute("INSERT INTO keywords(word) VALUES (?)", (word,)).lastrowid
        cur = conn.execute(
            "INSERT INTO photos_keywords(photo_id, keyword_id, src) VALUES (?,?,?) "
            "ON CONFLICT(photo_id, keyword_id) DO UPDATE SET src=MAX(src, excluded.src)",
            (photo_id, kid, src),
        )
        added += cur.rowcount
    return added


def photo_keywords(conn: sqlite3.Connection, photo_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT k.word FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id WHERE pk.photo_id=? ORDER BY k.word",
        (photo_id,),
    ).fetchall()
    return [r["word"] for r in rows]
