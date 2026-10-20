"""Unione di foto che descrivono lo stesso scatto (stesso DocumentID XMP, oppure stessi data/GPS/seriale)."""

from __future__ import annotations

import sqlite3

from mosaic.index.indexer import refresh_primary
from mosaic.meta import sources


def merge_photos(conn: sqlite3.Connection, keep: int, drop: int) -> None:
    """Sposta i file di `drop` dentro `keep` e rimuove `drop`. Etichette e parole chiave di `drop` si perdono."""
    if keep == drop:
        return
    conn.execute("UPDATE files SET photo_id=? WHERE photo_id=?", (keep, drop))
    conn.execute("UPDATE markers SET photo_id=? WHERE photo_id=?", (keep, drop))
    conn.execute("UPDATE OR IGNORE photos_albums SET photo_id=? WHERE photo_id=?", (keep, drop))
    conn.execute("DELETE FROM photos_fts WHERE rowid=?", (drop,))
    conn.execute("DELETE FROM photos WHERE id=?", (drop,))
    refresh_primary(conn, keep)


def find_twin(conn: sqlite3.Connection, photo_id: int, by_uuid: bool, by_meta: bool) -> int | None:
    photo = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if photo is None:
        return None
    if by_uuid and photo["uuid"]:
        row = conn.execute(
            "SELECT id FROM photos WHERE uuid=? AND id!=? AND deleted_at IS NULL ORDER BY id LIMIT 1",
            (photo["uuid"], photo_id),
        ).fetchone()
        if row:
            return row["id"]
    if (
        by_meta and photo["camera_serial"] and photo["lat"] is not None and photo["taken_at"]
        and photo["taken_src"] >= sources.META
    ):
        row = conn.execute(
            "SELECT id FROM photos WHERE id!=? AND deleted_at IS NULL AND taken_at=? AND lat=? AND lng=? "
            "AND camera_serial=? ORDER BY id LIMIT 1",
            (photo_id, photo["taken_at"], photo["lat"], photo["lng"], photo["camera_serial"]),
        ).fetchone()
        if row:
            return row["id"]
    return None


def stack_photo(conn: sqlite3.Connection, photo_id: int, by_uuid: bool, by_meta: bool) -> int:
    """Unisce la foto al suo gemello, se esiste. Ritorna l'id della foto che resta."""
    twin = find_twin(conn, photo_id, by_uuid, by_meta)
    if twin is None:
        return photo_id
    keep, drop = min(twin, photo_id), max(twin, photo_id)
    merge_photos(conn, keep, drop)
    return keep
