"""Rotte delle raccolte: album, etichette, persone e volti, duplicati, calendario."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mosaic.ai import neardup
from mosaic.api.deps import get_mosaic, summary
from mosaic.api.schemas import AlbumCreate, AlbumPatch, CurateRequest, Name, SubjectMerge, SubjectPatch, UidList
from mosaic.llm.backends import NO_CAPTIONER, captioning_available
from mosaic.meta import sources
from mosaic.organize import albums, curate, people
from mosaic.search import query
from mosaic.search.parser import QueryError, parse
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")


# --- album ---------------------------------------------------------------------------------

def _album(m: Mosaic, a: Any) -> dict[str, Any]:
    source = albums.album_by_key(m.ctx.db.conn(), a["source"]) if a["source"] else None
    return {"uid": a["uid"], "type": a["type"], "slug": a["slug"], "title": a["title"], "description": a["description"],
            "smart": bool(a["filter"]), "filter": a["filter"], "favorite": bool(a["favorite"]), "private": bool(a["private"]),
            "photo_count": a["photo_count"], "cover": albums.cover_hash(m.ctx, a),
            "source": {"uid": source["uid"], "title": source["title"], "type": source["type"]} if source else None}


def _album_or_404(m: Mosaic, ref: str) -> Any:
    album = albums.get_album(m.ctx.db.conn(), ref)
    if album is None or album["deleted_at"]:
        raise HTTPException(404, "album non trovato")
    return album


@router.get("/albums")
def list_albums(type: str | None = None, m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    if type is not None and type not in albums.TYPES:
        raise HTTPException(400, f"tipo di album non valido: {type}")
    return [_album(m, a) for a in albums.list_albums(m.ctx, type) if a["photo_count"] > 0 or a["type"] == "album"]


@router.post("/albums", status_code=201)
def create_album(body: AlbumCreate, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    source = ""
    if body.source:
        parent = albums.get_album(m.ctx.db.conn(), body.source)
        if parent is None or parent["deleted_at"]:
            raise HTTPException(404, "album di partenza non trovato")
        source = albums.source_key(parent)
    with m.ctx.db.tx() as conn:
        try:
            album = albums.create_album(conn, body.title, description=body.description, source=source)
        except albums.AlbumError as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.uids:
            ids = [r["id"] for r in conn.execute(
                f"SELECT id FROM photos WHERE uid IN ({','.join('?' * len(body.uids))})", body.uids)]
            albums.add_photos(conn, album["id"], ids)
    return _album(m, albums.get_album(m.ctx.db.conn(), album["id"]))


@router.get("/albums/{ref}")
def get_album(ref: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    return _album(m, _album_or_404(m, ref))


@router.get("/albums/{ref}/selections")
def album_selections(ref: str, m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    """Le selezioni salvate da questo album o cartella (D-27)."""
    return [_album(m, a) for a in albums.children(m.ctx.db.conn(), _album_or_404(m, ref))]


@router.patch("/albums/{ref}")
def patch_album(ref: str, body: AlbumPatch, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    album = _album_or_404(m, ref)
    cover_id = None
    if body.cover:
        row = m.ctx.db.one("SELECT id FROM photos WHERE uid=?", (body.cover,))
        if row is None:
            raise HTTPException(404, "foto di copertina non trovata")
        cover_id = row["id"]
    with m.ctx.db.tx() as conn:
        try:
            albums.update_album(conn, album["id"], title=body.title, description=body.description,
                                favorite=None if body.favorite is None else int(body.favorite),
                                private=None if body.private is None else int(body.private), cover_photo_id=cover_id)
        except albums.AlbumError as exc:
            raise HTTPException(400, str(exc)) from exc
    return _album(m, _album_or_404(m, ref))


@router.post("/albums/{ref}/caption", status_code=202)
def caption_album(ref: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    """Rigenera le didascalie delle foto dell'album con la giornata raccontata. XMP e testi manuali non si toccano."""
    album = _album_or_404(m, ref)
    if not captioning_available(m.ctx):
        raise HTTPException(400, NO_CAPTIONER)
    conn = m.ctx.db.conn()
    where, params = query.build_where(conn, parse(f"album:{album['uid']}"), default_visibility=False)
    ids = [r["id"] for r in conn.execute(
        f"SELECT p.id FROM photos p WHERE {where} AND p.deleted_at IS NULL "
        f"AND NOT (p.title_src>={sources.XMP} AND p.caption_src>={sources.XMP}) ORDER BY p.id", params)]
    if not ids:
        raise HTTPException(400, "nessuna foto da descrivere in questo album")
    # senza unique: ogni richiesta ha le sue foto e non deve fondersi con un job di didascalie già in coda
    return {"job": m.runner.submit("caption", {"ids": ids, "force": True}), "photos": len(ids)}


@router.post("/albums/{ref}/curate")
def curate_album(ref: str, body: CurateRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """«Crea selezione» (D-21): le foto che riassumono l'album o la cartella, in ordine di scatto, con i motivi."""
    album = _album_or_404(m, ref)
    try:
        result = curate.curate(m.ctx, album["uid"], curate.Options(**body.model_dump()))
    except (curate.CurateError, QueryError) as exc:
        raise HTTPException(400, str(exc)) from exc
    rows = {r["id"]: r for r in query.photo_summaries(m.ctx, [p.id for p in result.picks])}
    photos = [{**summary(rows[p.id]), "reasons": p.reasons} for p in result.picks if p.id in rows]
    return {"album": _album(m, album), "photos": photos, "stats": result.stats}


@router.delete("/albums/{ref}", status_code=204)
def delete_album(ref: str, m: Mosaic = Depends(get_mosaic)) -> None:
    album = _album_or_404(m, ref)
    if album["type"] != "album":
        raise HTTPException(400, "gli album automatici si rigenerano da soli: non si eliminano")
    with m.ctx.db.tx() as conn:
        albums.delete_album(conn, album["id"])


def _ids(m: Mosaic, uids: list[str]) -> list[int]:
    return [r["id"] for r in m.ctx.db.all(f"SELECT id FROM photos WHERE uid IN ({','.join('?' * len(uids))})", tuple(uids))]


@router.post("/albums/{ref}/photos")
def add_to_album(ref: str, body: UidList, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    album = _album_or_404(m, ref)
    with m.ctx.db.tx() as conn:
        try:
            return {"changed": albums.add_photos(conn, album["id"], _ids(m, body.uids))}
        except albums.AlbumError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/albums/{ref}/photos/remove")
def remove_from_album(ref: str, body: UidList, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    album = _album_or_404(m, ref)
    with m.ctx.db.tx() as conn:
        return {"changed": albums.remove_photos(conn, album["id"], _ids(m, body.uids))}


@router.get("/calendar")
def calendar(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    """Mesi con foto, raggruppati per anno (dagli album 'month')."""
    years: dict[str, list[dict[str, Any]]] = {}
    for a in albums.list_albums(m.ctx, "month"):
        year, month = a["slug"].split("-")
        years.setdefault(year, []).append({"month": int(month), "title": a["title"], "count": a["photo_count"],
                                           "album": a["uid"], "cover": albums.cover_hash(m.ctx, a)})
    return [{"year": int(y), "months": sorted(ms, key=lambda x: x["month"])} for y, ms in sorted(years.items(), reverse=True)]


# --- etichette ---------------------------------------------------------------------------------

@router.get("/labels")
def labels(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    rows = m.ctx.db.all(
        "SELECT l.name, l.slug, l.category, COUNT(DISTINCT pl.photo_id) AS n, "
        "(SELECT f.sha1 FROM photos_labels x JOIN photos p ON p.id=x.photo_id JOIN files f ON f.photo_id=p.id AND f.is_primary=1 "
        " WHERE x.label_id=l.id AND p.deleted_at IS NULL AND p.private=0 ORDER BY x.uncertainty LIMIT 1) AS cover "
        "FROM labels l JOIN photos_labels pl ON pl.label_id=l.id JOIN photos p ON p.id=pl.photo_id "
        "WHERE p.deleted_at IS NULL AND p.private=0 GROUP BY l.id ORDER BY n DESC, l.name")
    return [{"name": r["name"], "slug": r["slug"], "category": r["category"], "count": r["n"], "cover": r["cover"]} for r in rows]


# --- persone e volti -----------------------------------------------------------------------------

def _best_marker(m: Mosaic, subject_id: int) -> str | None:
    row = m.ctx.db.one("SELECT uid FROM markers WHERE subj_id=? AND invalid=0 ORDER BY score DESC, size DESC LIMIT 1", (subject_id,))
    return row["uid"] if row else None


@router.get("/subjects")
def subjects(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    rows = m.ctx.db.all("SELECT * FROM subjects WHERE photo_count>0 ORDER BY hidden, favorite DESC, photo_count DESC, name")
    return [{"uid": s["uid"], "name": s["name"], "slug": s["slug"], "photo_count": s["photo_count"], "hidden": bool(s["hidden"]),
             "favorite": bool(s["favorite"]), "marker": _best_marker(m, s["id"])} for s in rows]


@router.get("/faces")
def unnamed_faces(limit: int = 120, m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    """Gruppi di volti riconosciuti ma ancora senza nome, dal più numeroso."""
    rows = m.ctx.db.all(
        "SELECT f.id, f.samples, (SELECT uid FROM markers WHERE face_id=f.id AND invalid=0 ORDER BY score DESC, size DESC LIMIT 1) AS marker "
        "FROM faces f WHERE f.subj_id IS NULL AND f.hidden=0 ORDER BY f.samples DESC LIMIT ?", (max(1, min(limit, 500)),))
    return [{"face": r["id"], "samples": r["samples"], "marker": r["marker"]} for r in rows if r["marker"]]


@router.get("/faces/suggestions")
def face_suggestions(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    """Persone proposte per volti ancora senza nome, raggruppate per persona e dal più vicino."""
    return people.list_suggestions(m.ctx.db.conn())


@router.post("/faces/suggestions/confirm")
def confirm_suggestions(body: UidList, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Conferma molti suggerimenti insieme; quelli non più validi (già confermati o rifiutati) si segnalano e si saltano."""
    skipped: list[str] = []
    with m.ctx.db.tx() as conn:
        for uid in body.uids:
            try:
                people.confirm_suggestion(conn, uid)
            except people.PeopleError:
                skipped.append(uid)
    return {"confirmed": len(body.uids) - len(skipped), "skipped": skipped}


def _subject_or_404(m: Mosaic, uid: str) -> Any:
    row = m.ctx.db.one("SELECT * FROM subjects WHERE uid=?", (uid,))
    if row is None:
        raise HTTPException(404, "persona non trovata")
    return row


@router.patch("/subjects/{uid}")
def patch_subject(uid: str, body: SubjectPatch, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    subject = _subject_or_404(m, uid)
    with m.ctx.db.tx() as conn:
        try:
            if body.name is not None:
                people.rename_subject(conn, subject["id"], body.name)
        except people.PeopleError as exc:
            raise HTTPException(400, str(exc)) from exc
        for column, value in (("hidden", body.hidden), ("favorite", body.favorite)):
            if value is not None:
                conn.execute(f"UPDATE subjects SET {column}=? WHERE id=?", (int(value), subject["id"]))
    updated = m.ctx.db.one("SELECT uid, name, slug, hidden, favorite, photo_count FROM subjects WHERE uid=?", (uid,))
    return {**dict(updated), "hidden": bool(updated["hidden"]), "favorite": bool(updated["favorite"])}


@router.post("/subjects/merge")
def merge_subjects(body: SubjectMerge, m: Mosaic = Depends(get_mosaic)) -> dict[str, str]:
    keep, drop = _subject_or_404(m, body.keep), _subject_or_404(m, body.drop)
    with m.ctx.db.tx() as conn:
        try:
            people.merge_subjects(conn, keep["id"], drop["id"])
        except people.PeopleError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"uid": keep["uid"]}


@router.post("/faces/{face_id}/name")
def name_face(face_id: str, body: Name, m: Mosaic = Depends(get_mosaic)) -> dict[str, str]:
    with m.ctx.db.tx() as conn:
        try:
            subject = people.name_face(conn, face_id, body.name)
        except (people.PeopleError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"subject": conn.execute("SELECT uid FROM subjects WHERE id=?", (subject,)).fetchone()["uid"]}


@router.post("/markers/{uid}/name")
def name_marker(uid: str, body: Name, m: Mosaic = Depends(get_mosaic)) -> dict[str, str]:
    with m.ctx.db.tx() as conn:
        try:
            subject = people.name_marker(conn, uid, body.name)
        except (people.PeopleError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"subject": conn.execute("SELECT uid FROM subjects WHERE id=?", (subject,)).fetchone()["uid"]}


@router.post("/markers/{uid}/confirm")
def confirm_marker(uid: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, str]:
    with m.ctx.db.tx() as conn:
        try:
            subject = people.confirm_suggestion(conn, uid)
        except people.PeopleError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"subject": conn.execute("SELECT uid FROM subjects WHERE id=?", (subject,)).fetchone()["uid"]}


@router.post("/markers/{uid}/suggestion/reject", status_code=204)
def reject_suggestion(uid: str, m: Mosaic = Depends(get_mosaic)) -> None:
    with m.ctx.db.tx() as conn:
        try:
            people.reject_suggestion(conn, uid)
        except people.PeopleError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/markers/{uid}/reject", status_code=204)
def reject_marker(uid: str, m: Mosaic = Depends(get_mosaic)) -> None:
    with m.ctx.db.tx() as conn:
        try:
            people.reject_marker(conn, uid)
        except people.PeopleError as exc:
            raise HTTPException(404, str(exc)) from exc


# --- duplicati ----------------------------------------------------------------------------------

@router.get("/duplicates")
def duplicates(kind: str = "exact", m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    if kind == "near":
        groups = []
        for ids in neardup.near_duplicate_groups(m.ctx):
            shown = query.photo_summaries(m.ctx, ids)
            if len(shown) > 1:
                groups.append({"photos": [summary(p) for p in shown]})
        return {"kind": kind, "groups": groups}
    if kind != "exact":
        raise HTTPException(400, "kind deve essere 'exact' o 'near'")
    by_photo: dict[int, list[dict[str, Any]]] = {}
    for d in m.ctx.db.all("SELECT d.*, f.photo_id FROM duplicates d JOIN files f ON f.id=d.of_file_id"):
        path = Path(d["root"]) / d["dir"] / d["name"] if d["dir"] else Path(d["root"]) / d["name"]
        by_photo.setdefault(d["photo_id"], []).append({"path": str(path), "size": d["size"], "exists": path.exists()})
    shown = {p["id"]: p for p in query.photo_summaries(m.ctx, list(by_photo))}
    return {"kind": kind, "groups": [{"photo": summary(shown[pid]), "copies": copies}
                                     for pid, copies in by_photo.items() if pid in shown]}
