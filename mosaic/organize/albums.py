"""Album manuali e smart album (filtro serializzato come query di ricerca)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.store import new_uid
from mosaic.search import query as search_query
from mosaic.textutil import slugify

AUTO_TYPES = ("folder", "month", "moment")
TYPES = ("album", *AUTO_TYPES)


class AlbumError(ValueError):
    pass


def unique_slug(conn: sqlite3.Connection, album_type: str, title: str) -> str:
    base = slugify(title) or "album"
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM albums WHERE type=? AND slug=?", (album_type, slug)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_album(conn: sqlite3.Connection, title: str, album_type: str = "album", description: str = "",
                 filter: str = "", slug: str | None = None, source: str = "") -> sqlite3.Row:
    title = title.strip()
    if not title:
        raise AlbumError("il titolo dell'album non può essere vuoto")
    if album_type not in TYPES:
        raise AlbumError(f"tipo di album non valido: {album_type}")
    now = utcnow()
    conn.execute(
        "INSERT INTO albums(uid, type, slug, title, description, filter, source, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (new_uid("a"), album_type, slug or unique_slug(conn, album_type, title), title, description, filter, source, now, now))
    return conn.execute("SELECT * FROM albums WHERE id=last_insert_rowid()").fetchone()


def get_album(conn: sqlite3.Connection, ref: str | int) -> sqlite3.Row | None:
    if isinstance(ref, int) or str(ref).isdigit():
        return conn.execute("SELECT * FROM albums WHERE id=?", (int(ref),)).fetchone()
    return conn.execute("SELECT * FROM albums WHERE uid=? OR slug=?", (ref, str(ref).lower())).fetchone()


def source_key(album: sqlite3.Row) -> str:
    """Chiave stabile di un album come origine di una selezione: tipo e slug (unici insieme)."""
    return f"{album['type']}:{album['slug']}"


def album_by_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    kind, _, slug = key.partition(":")
    return conn.execute("SELECT * FROM albums WHERE type=? AND slug=? AND deleted_at IS NULL", (kind, slug)).fetchone() if slug else None


def children(conn: sqlite3.Connection, album: sqlite3.Row) -> list[sqlite3.Row]:
    """Le selezioni salvate da questo album, dalla più recente."""
    return conn.execute("SELECT * FROM albums WHERE source=? AND deleted_at IS NULL ORDER BY created_at DESC, id DESC",
                        (source_key(album),)).fetchall()


def list_albums(ctx: Context, album_type: str | None = None, include_hidden: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM albums WHERE deleted_at IS NULL"
    params: list[Any] = []
    if album_type:
        sql += " AND type=?"
        params.append(album_type)
    if not include_hidden:
        sql += " AND private=0"
    order = "title COLLATE NOCASE" if album_type in (None, "album", "folder") else "slug DESC"
    return ctx.db.all(f"{sql} ORDER BY {order}", tuple(params))


def add_photos(conn: sqlite3.Connection, album_id: int, photo_ids: Iterable[int]) -> int:
    album = conn.execute("SELECT filter FROM albums WHERE id=?", (album_id,)).fetchone()
    if album is None:
        raise AlbumError("album inesistente")
    if album["filter"]:
        raise AlbumError("uno smart album si compone da solo: non accetta aggiunte manuali")
    pos = conn.execute("SELECT COALESCE(MAX(position),0) FROM photos_albums WHERE album_id=?", (album_id,)).fetchone()[0]
    added = 0
    for pid in photo_ids:
        if conn.execute("SELECT 1 FROM photos WHERE id=?", (pid,)).fetchone() is None:
            continue
        pos += 1
        added += conn.execute("INSERT OR IGNORE INTO photos_albums(album_id, photo_id, position, added_at) VALUES (?,?,?,?)",
                              (album_id, pid, pos, utcnow())).rowcount
    update_count(conn, album_id)
    return added


def remove_photos(conn: sqlite3.Connection, album_id: int, photo_ids: Iterable[int]) -> int:
    removed = sum(conn.execute("DELETE FROM photos_albums WHERE album_id=? AND photo_id=?", (album_id, pid)).rowcount
                  for pid in photo_ids)
    update_count(conn, album_id)
    return removed


def update_album(conn: sqlite3.Connection, album_id: int, **fields: Any) -> None:
    allowed = {"title", "description", "favorite", "private", "cover_photo_id"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "title" in changes and not str(changes["title"]).strip():
        raise AlbumError("il titolo dell'album non può essere vuoto")
    if changes:
        sets = ", ".join(f"{k}=?" for k in changes)
        conn.execute(f"UPDATE albums SET {sets}, updated_at=? WHERE id=?", (*changes.values(), utcnow(), album_id))


def delete_album(conn: sqlite3.Connection, album_id: int) -> None:
    conn.execute("DELETE FROM albums WHERE id=?", (album_id,))


def update_count(conn: sqlite3.Connection, album_id: int) -> None:
    conn.execute(
        "UPDATE albums SET photo_count=(SELECT COUNT(*) FROM photos_albums pa JOIN photos p ON p.id=pa.photo_id "
        "WHERE pa.album_id=albums.id AND p.deleted_at IS NULL), updated_at=? WHERE id=? AND filter=''", (utcnow(), album_id))


def refresh_counts(ctx: Context) -> None:
    """Aggiorna i conteggi: gli album manuali contano le righe, gli smart album eseguono la propria query."""
    conn = ctx.db.conn()
    for album in conn.execute("SELECT id, filter FROM albums WHERE deleted_at IS NULL").fetchall():
        if album["filter"]:
            try:
                count = search_query.count_matching(ctx, album["filter"])
            except Exception:  # un filtro non più valido non deve bloccare gli altri album
                count = 0
            conn.execute("UPDATE albums SET photo_count=? WHERE id=?", (count, album["id"]))
        else:
            update_count(conn, album["id"])


def cover_hash(ctx: Context, album: sqlite3.Row) -> str | None:
    """Hash del file principale della copertina, o della prima foto dell'album."""
    row = None
    if album["cover_photo_id"]:
        row = ctx.db.one("SELECT sha1 FROM files WHERE photo_id=? AND is_primary=1", (album["cover_photo_id"],))
    if row is None:
        if album["filter"]:
            found = search_query.search(ctx, f'album:{album["uid"]}', count=1, with_photos=True).photos
            return found[0]["hash"] if found else None
        row = ctx.db.one(
            "SELECT f.sha1 FROM photos_albums pa JOIN files f ON f.photo_id=pa.photo_id AND f.is_primary=1 "
            "JOIN photos p ON p.id=pa.photo_id WHERE pa.album_id=? AND p.deleted_at IS NULL ORDER BY pa.position LIMIT 1",
            (album["id"],))
    return row["sha1"] if row else None
