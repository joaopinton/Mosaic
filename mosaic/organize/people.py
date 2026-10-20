"""Persone (subjects): creazione, assegnazione dei nomi e conteggi."""

from __future__ import annotations

import sqlite3

from mosaic.db import utcnow
from mosaic.index.store import new_uid
from mosaic.meta import sources
from mosaic.textutil import slugify


def ensure_subject(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("il nome della persona non può essere vuoto")
    slug = slugify(name) or "persona"
    row = conn.execute("SELECT id FROM subjects WHERE slug=?", (slug,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO subjects(uid, name, slug, created_at) VALUES (?,?,?,?)", (new_uid("s"), name, slug, utcnow())
    ).lastrowid


def update_counts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE subjects SET marker_count=(SELECT COUNT(*) FROM markers m WHERE m.subj_id=subjects.id AND m.invalid=0), "
        "photo_count=(SELECT COUNT(DISTINCT m.photo_id) FROM markers m WHERE m.subj_id=subjects.id AND m.invalid=0)"
    )


class PeopleError(ValueError):
    pass


def rename_subject(conn: sqlite3.Connection, subject_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise PeopleError("il nome non può essere vuoto")
    slug = slugify(name) or "persona"
    clash = conn.execute("SELECT id FROM subjects WHERE slug=? AND id!=?", (slug, subject_id)).fetchone()
    if clash:
        raise PeopleError(f"esiste già una persona chiamata «{name}»: usa l'unione")
    conn.execute("UPDATE subjects SET name=?, slug=? WHERE id=?", (name, slug, subject_id))
    conn.execute("UPDATE markers SET name=? WHERE subj_id=?", (name, subject_id))


def merge_subjects(conn: sqlite3.Connection, keep: int, drop: int) -> None:
    """Unisce due persone: volti e cluster di `drop` passano a `keep`."""
    if keep == drop:
        return
    target = conn.execute("SELECT name FROM subjects WHERE id=?", (keep,)).fetchone()
    if target is None or conn.execute("SELECT 1 FROM subjects WHERE id=?", (drop,)).fetchone() is None:
        raise PeopleError("persona inesistente")
    conn.execute("UPDATE markers SET subj_id=?, name=? WHERE subj_id=?", (keep, target["name"], drop))
    conn.execute("UPDATE faces SET subj_id=? WHERE subj_id=?", (keep, drop))
    conn.execute("UPDATE markers SET sugg_subj_id=? WHERE sugg_subj_id=?", (keep, drop))
    conn.execute("DELETE FROM subjects WHERE id=?", (drop,))
    update_counts(conn)


def name_face(conn: sqlite3.Connection, face_id: str, name: str) -> int:
    """Dà un nome a un cluster di volti (e a tutti i suoi volti)."""
    if conn.execute("SELECT 1 FROM faces WHERE id=?", (face_id,)).fetchone() is None:
        raise PeopleError("cluster inesistente")
    subject = ensure_subject(conn, name)
    label = conn.execute("SELECT name FROM subjects WHERE id=?", (subject,)).fetchone()["name"]
    conn.execute("UPDATE faces SET subj_id=? WHERE id=?", (subject, face_id))
    conn.execute("UPDATE markers SET subj_id=?, name=?, src=MAX(src, ?), sugg_subj_id=NULL, sugg_dist=-1 WHERE face_id=?",
                 (subject, label, sources.MANUAL, face_id))
    update_counts(conn)
    return subject


def name_marker(conn: sqlite3.Connection, marker_uid: str, name: str) -> int:
    """Dà un nome a un singolo volto (le altre foto della stessa persona si riconoscono con l'esecuzione dei volti)."""
    row = conn.execute("SELECT id FROM markers WHERE uid=?", (marker_uid,)).fetchone()
    if row is None:
        raise PeopleError("volto inesistente")
    subject = ensure_subject(conn, name)
    label = conn.execute("SELECT name FROM subjects WHERE id=?", (subject,)).fetchone()["name"]
    conn.execute("UPDATE markers SET subj_id=?, name=?, invalid=0, src=MAX(src, ?), sugg_subj_id=NULL, sugg_dist=-1 WHERE id=?",
                 (subject, label, sources.MANUAL, row["id"]))
    update_counts(conn)
    return subject


def reject_marker(conn: sqlite3.Connection, marker_uid: str) -> None:
    """Segna un riquadro come falso positivo: non è un volto e non verrà più proposto."""
    cur = conn.execute("UPDATE markers SET invalid=1, subj_id=NULL, name='', face_id=NULL, face_dist=-1, "
                       "sugg_subj_id=NULL, sugg_dist=-1 WHERE uid=?", (marker_uid,))
    if cur.rowcount == 0:
        raise PeopleError("volto inesistente")
    update_counts(conn)


def _suggested(conn: sqlite3.Connection, marker_uid: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, sugg_subj_id, sugg_rejected FROM markers WHERE uid=? AND invalid=0", (marker_uid,)).fetchone()
    if row is None:
        raise PeopleError("volto inesistente")
    if row["sugg_subj_id"] is None:
        raise PeopleError("nessun suggerimento per questo volto")
    return row


def confirm_suggestion(conn: sqlite3.Connection, marker_uid: str) -> int:
    """Conferma la persona suggerita: il volto prende il nome come se l'avesse dato l'utente."""
    row = _suggested(conn, marker_uid)
    name = conn.execute("SELECT name FROM subjects WHERE id=?", (row["sugg_subj_id"],)).fetchone()["name"]
    return name_marker(conn, marker_uid, name)


def reject_suggestion(conn: sqlite3.Connection, marker_uid: str) -> None:
    """Scarta il suggerimento e ricorda la persona rifiutata, perché non venga riproposta per questo volto."""
    row = _suggested(conn, marker_uid)
    rejected = [r for r in row["sugg_rejected"].split(",") if r]
    if str(row["sugg_subj_id"]) not in rejected:
        rejected.append(str(row["sugg_subj_id"]))
    conn.execute("UPDATE markers SET sugg_subj_id=NULL, sugg_dist=-1, sugg_rejected=? WHERE id=?",
                 (",".join(rejected), row["id"]))


def list_suggestions(conn: sqlite3.Connection) -> list[dict]:
    """Suggerimenti da confermare, raggruppati per persona; dentro ogni gruppo dal più vicino."""
    groups: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT m.uid, m.sugg_dist, s.id AS sid, s.uid AS suid, s.name FROM markers m JOIN subjects s ON s.id=m.sugg_subj_id "
        "WHERE m.invalid=0 AND m.subj_id IS NULL ORDER BY m.sugg_dist, m.id"):
        group = groups.setdefault(r["sid"], {"subject": r["suid"], "name": r["name"], "markers": []})
        group["markers"].append({"uid": r["uid"], "dist": round(r["sugg_dist"], 3)})
    return sorted(groups.values(), key=lambda g: (g["markers"][0]["dist"], g["name"]))


def faces_count(conn: sqlite3.Connection, photo_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM markers WHERE photo_id=? AND invalid=0", (photo_id,)).fetchone()[0]
