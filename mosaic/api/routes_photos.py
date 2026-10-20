"""Rotte delle foto: elenco/ricerca, dettaglio, modifica, azioni di gruppo, simili e suggerimenti."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from mosaic.api.deps import get_mosaic, photo_or_404, summary
from mosaic.api.schemas import BatchAction, PhotoPatch, RemovePhotos
from mosaic.db import utcnow
from mosaic.index.store import abs_path
from mosaic.meta import sources
from mosaic.organize import albums, catalog, quality, tags
from mosaic.search import fts, query
from mosaic.search.parser import KNOWN_KEYS, QueryError
from mosaic.search.similar import similar_photos
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")


@router.get("/photos")
def list_photos(q: str = "", count: int = Query(60, ge=1, le=500), offset: int = Query(0, ge=0), order: str = "",
                reverse: bool = False, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    try:
        result = query.search(m.ctx, q, count, offset, order, reverse)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"total": result.total, "semantic": result.semantic, "photos": [summary(p) for p in result.photos]}


def _detail(m: Mosaic, p: sqlite3.Row) -> dict[str, Any]:
    conn = m.ctx.db.conn()
    pid = p["id"]
    camera = conn.execute("SELECT make, model FROM cameras WHERE id=?", (p["camera_id"],)).fetchone() if p["camera_id"] else None
    lens = conn.execute("SELECT name FROM lenses WHERE id=?", (p["lens_id"],)).fetchone() if p["lens_id"] else None
    files = conn.execute("SELECT * FROM files WHERE photo_id=? ORDER BY is_primary DESC, id", (pid,)).fetchall()
    primary = next((f for f in files if f["is_primary"]), files[0] if files else None)
    return {
        "uid": p["uid"], "type": p["type"], "title": p["title"], "title_source": sources.NAMES.get(p["title_src"], ""),
        "caption": p["caption"], "caption_source": sources.NAMES.get(p["caption_src"], ""),
        "taken_at": p["taken_at_local"], "time_zone": p["time_zone"], "taken_source": sources.NAMES.get(p["taken_src"], ""),
        "lat": p["lat"], "lng": p["lng"], "altitude": p["altitude"],
        "camera": {"make": camera["make"], "model": camera["model"]} if camera else None, "lens": lens["name"] if lens else "",
        "iso": p["iso"], "exposure": p["exposure"], "f_number": p["f_number"], "focal_length": p["focal_length"],
        "width": p["width"], "height": p["height"], "duration": p["duration"], "rating": p["rating"],
        "favorite": bool(p["favorite"]), "private": bool(p["private"]), "archived": p["deleted_at"] is not None,
        "quality": p["quality"], "review": quality.is_review(p["quality"]), "main_color": p["main_color"],
        "colors": p["colors"].split(), "hash": primary["sha1"] if primary else "", "seq": p["seq"],
        "files": [{"name": f["name"], "original_name": f["original_name"], "path": str(abs_path(f)),
                   "media_type": f["media_type"], "size": f["size"], "width": f["width"], "height": f["height"],
                   "codec": f["codec"], "primary": bool(f["is_primary"]), "missing": bool(f["missing"])} for f in files],
        "labels": [{"name": r["name"], "slug": r["slug"], "category": r["category"], "confidence": 100 - r["uncertainty"],
                    "source": sources.NAMES.get(r["src"], "")} for r in tags.photo_labels(conn, pid)],
        "keywords": tags.photo_keywords(conn, pid),
        "people": [{"marker": r["uid"], "name": r["name"], "subject": r["suid"], "face": r["face_id"],
                    "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]} for r in conn.execute(
            "SELECT m.uid, m.name, m.face_id, m.x, m.y, m.w, m.h, s.uid AS suid FROM markers m "
            "LEFT JOIN subjects s ON s.id=m.subj_id WHERE m.photo_id=? AND m.invalid=0 ORDER BY m.x", (pid,))],
        "albums": [{"uid": r["uid"], "title": r["title"]} for r in conn.execute(
            "SELECT a.uid, a.title FROM albums a JOIN photos_albums pa ON pa.album_id=a.id WHERE pa.photo_id=?", (pid,))],
    }


@router.get("/photos/{uid}")
def photo_detail(uid: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    return _detail(m, photo_or_404(m, uid))


@router.patch("/photos/{uid}")
def patch_photo(uid: str, body: PhotoPatch, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    p = photo_or_404(m, uid)
    pid = p["id"]
    with m.ctx.db.tx() as conn:
        for field in ("title", "caption"):
            value = getattr(body, field)
            if value is None:
                continue
            if value.strip():
                sources.set_text(conn, pid, field, value, sources.MANUAL)
            else:  # svuotare un campo lo restituisce all'automatismo
                conn.execute(f"UPDATE photos SET {field}='', {field}_src=0 WHERE id=?", (pid,))
        if body.favorite is not None:
            conn.execute("UPDATE photos SET favorite=? WHERE id=?", (int(body.favorite), pid))
        if body.private is not None:
            conn.execute("UPDATE photos SET private=? WHERE id=?", (int(body.private), pid))
        tags.add_keywords(conn, pid, body.keywords_add, sources.MANUAL)
        for word in body.keywords_remove:
            conn.execute("DELETE FROM photos_keywords WHERE photo_id=? AND keyword_id IN (SELECT id FROM keywords WHERE word=?)",
                         (pid, word.strip().lower()))
        for name in body.labels_add:
            if name.strip():
                tags.set_photo_label(conn, pid, tags.get_or_create_label(conn, name), 0, sources.MANUAL)
        for slug in body.labels_remove:
            conn.execute("DELETE FROM photos_labels WHERE photo_id=? AND label_id IN (SELECT id FROM labels WHERE slug=?)",
                         (pid, slug))
        quality.update(conn, pid)
        conn.execute("UPDATE photos SET updated_at=? WHERE id=?", (utcnow(), pid))
        fts.reindex(conn, pid)
    return _detail(m, photo_or_404(m, uid))


@router.post("/photos/batch")
def batch(body: BatchAction, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    marks = ",".join("?" * len(body.uids))
    ids = [r["id"] for r in m.ctx.db.all(f"SELECT id FROM photos WHERE uid IN ({marks})", tuple(body.uids))]
    if not ids:
        raise HTTPException(404, "nessuna foto trovata")
    album = None
    if body.action in ("add_to_album", "remove_from_album"):
        album = albums.get_album(m.ctx.db.conn(), body.album or "")
        if album is None:
            raise HTTPException(404, "album non trovato")
    updates = {"favorite": ("favorite=?", 1), "unfavorite": ("favorite=?", 0), "private": ("private=?", 1),
               "public": ("private=?", 0), "archive": ("deleted_at=?", utcnow()), "restore": ("deleted_at=?", None)}
    with m.ctx.db.tx() as conn:
        if album is not None:
            try:
                changed = (albums.add_photos if body.action == "add_to_album" else albums.remove_photos)(conn, album["id"], ids)
            except albums.AlbumError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"changed": changed}
        id_marks = ",".join("?" * len(ids))
        assignment, value = updates[body.action]  # colonna fissa nel codice, valore sempre come parametro
        cur = conn.execute(f"UPDATE photos SET {assignment}, updated_at=? WHERE id IN ({id_marks})", (value, utcnow(), *ids))
        for pid in ids:
            quality.update(conn, pid)
    return {"changed": cur.rowcount}


@router.post("/photos/remove")
def remove(body: RemovePhotos, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Toglie le foto dal catalogo (D-20). Con `delete_mode` "trash" i file vanno nel Cestino di Windows, mai cancellati."""
    marks = ",".join("?" * len(body.uids))
    rows = m.ctx.db.all(f"SELECT id, uid FROM photos WHERE uid IN ({marks})", tuple(body.uids))
    if not rows:
        raise HTTPException(404, "nessuna foto trovata")
    mode = m.cfg.delete_mode
    result = catalog.remove_photos(m.ctx, [r["id"] for r in rows], mode)
    uid_of = {r["id"]: r["uid"] for r in rows}
    if result.removed:
        m.runner.submit("moments", {}, unique=True)  # gli album automatici si rifanno senza queste foto
    return {"removed": result.removed, "files": result.files, "mode": mode,
            "failed": [{"uid": uid_of[pid], "error": error} for pid, error in result.failed]}


@router.get("/photos/{uid}/similar")
def similar(uid: str, k: int = Query(8, ge=1, le=40), m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    p = photo_or_404(m, uid)
    hits = similar_photos(m.ctx, p["id"], k)
    by_id = {s["id"]: s for s in query.photo_summaries(m.ctx, [h.photo_id for h in hits])}
    return {"ready": bool(hits) or m.ctx.db.scalar("SELECT 1 FROM embeddings WHERE photo_id=?", (p["id"],)) is not None,
            "hits": [{"photo": summary(by_id[h.photo_id]), "score": h.score, "cosine": h.cosine, "reasons": h.reasons}
                     for h in hits if h.photo_id in by_id]}


@router.get("/search/suggest")
def suggest(q: str = "", m: Mosaic = Depends(get_mosaic)) -> dict[str, list[str]]:
    """Suggerimenti per la barra di ricerca: chiavi mentre si scrive, valori dopo i due punti."""
    token = q.split(" ")[-1] if q else ""
    conn = m.ctx.db.conn()
    if ":" not in token:
        head = token.lower().lstrip("-")
        return {"keys": sorted(k + ":" for k in KNOWN_KEYS if head and k.startswith(head))[:8], "values": []}
    key, _, partial = token.lstrip("-").partition(":")
    needle = f"%{partial.lower()}%"
    sql = {
        "label": "SELECT slug FROM labels WHERE lower(name) LIKE ? ORDER BY name LIMIT 8",
        "category": "SELECT DISTINCT lower(category) FROM labels WHERE category!='' AND lower(category) LIKE ? LIMIT 8",
        "person": "SELECT name FROM subjects WHERE lower(name) LIKE ? ORDER BY photo_count DESC LIMIT 8",
        "album": "SELECT slug FROM albums WHERE deleted_at IS NULL AND lower(title) LIKE ? ORDER BY title LIMIT 8",
        "camera": "SELECT trim(make||' '||model) FROM cameras WHERE lower(make||' '||model) LIKE ? LIMIT 8",
        "folder": "SELECT path FROM folders WHERE path!='' AND lower(path) LIKE ? ORDER BY path LIMIT 8",
        "keyword": "SELECT word FROM keywords WHERE word LIKE ? ORDER BY word LIMIT 8",
    }.get(key)
    if sql is None:
        fixed = {"type": ["image", "video", "live", "animated", "raw"], "color": ["rosso", "arancione", "giallo", "verde", "azzurro",
                 "blu", "viola", "rosa", "marrone", "nero", "bianco", "grigio"]}.get(key, [])
        return {"keys": [], "values": [v for v in fixed if partial.lower() in v]}
    return {"keys": [], "values": [r[0] for r in conn.execute(sql, (needle,))]}
