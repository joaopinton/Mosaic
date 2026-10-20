"""Backup YAML dei dati inseriti a mano, un file per foto (strategia di internal/entity/photo_yaml.go di PhotoPrism).

I file stanno in data/backup/<sha1[:2]>/<sha1>.yml, indicizzati dall'hash del file principale: se il database va perso
o la foto viene reindicizzata, titolo, didascalia, parole chiave, etichette, preferiti e persone tornano da soli.
Si salvano solo i dati di priorità manuale (o superiore): quelli automatici si rifanno con l'analisi.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.store import new_uid, primary_file
from mosaic.meta import sources
from mosaic.organize import albums, tags
from mosaic.organize.people import ensure_subject, update_counts
from mosaic.search import fts

log = logging.getLogger(__name__)
VERSION = 1
LAST_KEY = "backup_at"


def backup_path(ctx: Context, sha1: str) -> Path:
    return ctx.backup_dir / sha1[:2] / f"{sha1}.yml"


def snapshot(conn: sqlite3.Connection, photo_id: int) -> dict[str, Any]:
    """Dati manuali di una foto, o un dizionario vuoto se non ce ne sono."""
    photo = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if photo is None:
        return {}
    data: dict[str, Any] = {}
    if photo["title_src"] >= sources.MANUAL and photo["title"]:
        data["title"] = photo["title"]
    if photo["caption_src"] >= sources.MANUAL and photo["caption"]:
        data["caption"] = photo["caption"]
    if photo["favorite"]:
        data["favorite"] = True
    if photo["private"] and not photo["nsfw"]:
        data["private"] = True
    if photo["rating"]:
        data["rating"] = photo["rating"]
    keywords = [r["word"] for r in conn.execute(
        "SELECT k.word FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id WHERE pk.photo_id=? AND pk.src>=? "
        "ORDER BY k.word", (photo_id, sources.MANUAL))]
    if keywords:
        data["keywords"] = keywords
    labels = [{"name": r["name"], "category": r["category"]} for r in conn.execute(
        "SELECT l.name, l.category FROM photos_labels pl JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id=? AND pl.src>=? "
        "ORDER BY l.name", (photo_id, sources.MANUAL))]
    if labels:
        data["labels"] = labels
    people = [{"name": r["name"], "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]} for r in conn.execute(
        "SELECT name, x, y, w, h FROM markers WHERE photo_id=? AND invalid=0 AND name!='' ORDER BY x", (photo_id,))]
    if people:
        data["people"] = people
    album_titles = [r["title"] for r in conn.execute(
        "SELECT a.title FROM albums a JOIN photos_albums pa ON pa.album_id=a.id WHERE pa.photo_id=? AND a.type='album' "
        "AND a.filter='' ORDER BY a.title", (photo_id,))]
    if album_titles:
        data["albums"] = album_titles
    if data:
        data["version"] = VERSION
    return data


def write_backup(ctx: Context, photo_id: int) -> bool:
    """Scrive (o rimuove, se non c'è più nulla di manuale) il backup di una foto. Ritorna True se il file esiste."""
    main = primary_file(ctx.db, photo_id)
    if main is None or not main["sha1"]:
        return False
    path = backup_path(ctx, main["sha1"])
    data = snapshot(ctx.db.conn(), photo_id)
    if not data:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
    return True


def backup_changed(ctx: Context) -> int:
    """Fa il backup delle foto modificate dall'ultimo passaggio. Ritorna quanti file ha scritto."""
    conn = ctx.db.conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (LAST_KEY,)).fetchone()
    since = row["value"] if row else ""
    started = utcnow()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM photos WHERE deleted_at IS NULL AND updated_at>=? ORDER BY id", (since,))]
    written = sum(1 for pid in ids if write_backup(ctx, pid))
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (LAST_KEY, started))
    return written


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("backup illeggibile %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def restore(ctx: Context, photo_id: int) -> bool:
    """Riapplica il backup a una foto appena creata. Non sovrascrive mai dati di priorità superiore."""
    main = primary_file(ctx.db, photo_id)
    if main is None:
        return False
    path = backup_path(ctx, main["sha1"])
    data = _load(path) if path.exists() else None
    if not data:
        return False
    with ctx.db.tx() as conn:
        sources.set_text(conn, photo_id, "title", str(data.get("title", "")), sources.MANUAL)
        sources.set_text(conn, photo_id, "caption", str(data.get("caption", "")), sources.MANUAL)
        if data.get("favorite"):
            conn.execute("UPDATE photos SET favorite=1 WHERE id=?", (photo_id,))
        if data.get("private"):
            conn.execute("UPDATE photos SET private=1 WHERE id=?", (photo_id,))
        if isinstance(data.get("rating"), int):
            conn.execute("UPDATE photos SET rating=? WHERE id=? AND rating=0", (data["rating"], photo_id))
        tags.add_keywords(conn, photo_id, [str(k) for k in data.get("keywords", [])], sources.MANUAL)
        for label in data.get("labels", []):
            if isinstance(label, dict) and label.get("name"):
                lid = tags.get_or_create_label(conn, str(label["name"]), str(label.get("category", "")))
                tags.set_photo_label(conn, photo_id, lid, 0, sources.MANUAL)
        if data.get("people") and not conn.execute("SELECT 1 FROM markers WHERE photo_id=?", (photo_id,)).fetchone():
            for person in data["people"]:
                if not isinstance(person, dict) or not person.get("name"):
                    continue
                subj = ensure_subject(conn, str(person["name"]))
                conn.execute(
                    "INSERT INTO markers(uid, file_id, photo_id, src, name, subj_id, x, y, w, h, size, score) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,0,100)",
                    (new_uid("m"), main["id"], photo_id, sources.MANUAL, person["name"], subj,
                     float(person.get("x", 0)), float(person.get("y", 0)), float(person.get("w", 0.1)), float(person.get("h", 0.1))))
            update_counts(conn)
        for title in data.get("albums", []):
            album = conn.execute("SELECT id FROM albums WHERE type='album' AND filter='' AND lower(title)=lower(?)",
                                 (str(title),)).fetchone()
            album_id = album["id"] if album else albums.create_album(conn, str(title))["id"]
            albums.add_photos(conn, album_id, [photo_id])
        fts.reindex(conn, photo_id)
    return True
