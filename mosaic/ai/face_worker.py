"""Worker dei volti: clustering e riconoscimento sul modello di PhotoPrism (internal/photoprism/faces.go).

Passi, in ordine:
 1 marker orfani  2 riferimenti rotti  3 persone dai nomi  4 collisioni  5 unione dei cluster della stessa persona
 6 match sui cluster esistenti  7 clustering dei rimasti, solo volti frontali (core da models.yaml)
 8 retry (core ridotto) e nuovo match  9 riconoscimento dalle persone con un nome (k-NN: assegnazione o suggerimento)
 10 una persona per foto  11 consenso sui nomi  12 pulizia  13 conteggi.
A differenza di PhotoPrism, il match sui cluster esistenti precede il clustering, così i volti delle foto nuove
non generano cluster duplicati accanto a quelli già noti.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from mosaic.ai import cluster
from mosaic.ai.cluster import EPSILON, FaceRef
from mosaic.ai.faces import foreground, frontal
from mosaic.ai.registry import FaceSpec, face_spec
from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.meta import sources
from mosaic.organize.people import ensure_subject, update_counts

log = logging.getLogger(__name__)

REEMBED_SIZES = ("fit_1920", "fit_720")  # immagini da cui si rifà l'embedding: la prima disponibile
KNN = 3                                  # volti nominati confrontati per ogni persona (media dei k più vicini)


@dataclass
class FaceStats:
    background: int = 0     # volti automatici sullo sfondo tolti (D-22)
    orphans: int = 0
    named: int = 0
    collisions: int = 0
    merged: int = 0
    matched: int = 0
    clusters: int = 0
    retry_clusters: int = 0
    subjects: int = 0       # volti nominati in automatico dalle persone note
    suggested: int = 0      # volti con un suggerimento da confermare
    deduplicated: int = 0   # nomi tolti perché la stessa persona compariva due volte in una foto
    consensus: int = 0
    removed: int = 0


def _vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _markers_with_embedding(conn: sqlite3.Connection, spec: FaceSpec, where: str = "1", params: tuple = ()) -> list[sqlite3.Row]:
    """`where` è sempre un frammento fisso del codice: i valori passano solo da `params`."""
    return conn.execute(
        f"SELECT * FROM markers WHERE invalid=0 AND embedding IS NOT NULL AND embed_model=? AND {where}",
        (spec.recognizer.id, *params),
    ).fetchall()


def _face_refs(conn: sqlite3.Connection, spec: FaceSpec) -> list[FaceRef]:
    rows = conn.execute("SELECT * FROM faces WHERE embed_model=? AND hidden=0", (spec.recognizer.id,)).fetchall()
    return [FaceRef(r["id"], _vec(r["embedding"]), r["sample_radius"], r["collision_radius"], r["subj_id"]) for r in rows]


def _assign(conn: sqlite3.Connection, marker_id: int, face_id: str, dist: float) -> None:
    conn.execute("UPDATE markers SET face_id=?, face_dist=?, matched_at=? WHERE id=?", (face_id, dist, utcnow(), marker_id))


def _unassign(conn: sqlite3.Connection, marker_id: int) -> None:
    conn.execute("UPDATE markers SET face_id=NULL, face_dist=-1 WHERE id=?", (marker_id,))


def _recompute_face(conn: sqlite3.Connection, face_id: str, spec: FaceSpec) -> None:
    rows = _markers_with_embedding(conn, spec, "face_id=?", (face_id,))
    if not rows:
        return
    vectors = np.stack([_vec(r["embedding"]) for r in rows])
    centroid = cluster.make_centroid(vectors)
    radius = cluster.cluster_radius(vectors, centroid, spec.recognizer.cluster_radius)
    conn.execute("UPDATE faces SET embedding=?, sample_radius=?, samples=? WHERE id=?",
                 (centroid.tobytes(), radius, len(rows), face_id))


# --- cambio di modello -------------------------------------------------------------------

@dataclass(frozen=True)
class ReembedStats:
    done: int = 0
    skipped: int = 0


def _stale_markers(conn: sqlite3.Connection, spec: FaceSpec) -> list[sqlite3.Row]:
    """Marker validi con punti del viso il cui embedding viene da un altro modello."""
    return conn.execute(
        "SELECT id, photo_id, x, y, w, h, score, landmarks FROM markers "
        "WHERE invalid=0 AND landmarks!='' AND embed_model!=? ORDER BY photo_id, id", (spec.recognizer.id,)).fetchall()


def pending_reembed(ctx: Context) -> int:
    """Quanti marker hanno un embedding di un modello diverso da quello attivo."""
    return len(_stale_markers(ctx.db.conn(), face_spec(ctx.models_dir)))


def _marker_image(ctx: Context, photo_id: int) -> np.ndarray | None:
    from PIL import Image

    from mosaic.ai.faces import pil_to_bgr
    from mosaic.pipeline import thumb_source

    for size in REEMBED_SIZES:
        try:
            path = thumb_source(ctx, photo_id, size)
            if path is not None:
                with Image.open(path) as img:
                    return pil_to_bgr(img)
        except Exception as exc:
            log.warning("immagine %s della foto %s non leggibile: %s", size, photo_id, exc)
    return None


def _yunet_rows(markers: list[sqlite3.Row], width: int, height: int) -> np.ndarray:
    """Righe nel formato YuNet (pixel dell'immagine `width`×`height`) da riquadri e punti normalizzati."""
    rows = np.zeros((len(markers), 15), dtype=np.float32)
    for i, m in enumerate(markers):
        rows[i, :4] = (m["x"] * width, m["y"] * height, m["w"] * width, m["h"] * height)
        for j, (px, py) in enumerate(json.loads(m["landmarks"])):
            rows[i, 4 + 2 * j], rows[i, 5 + 2 * j] = px * width, py * height
        rows[i, 14] = m["score"] / 100
    return rows


def reembed_markers(ctx: Context, engine=None, progress: Callable[..., None] | None = None,  # type: ignore[no-untyped-def]
                    is_cancelled: Callable[[], bool] = lambda: False) -> ReembedStats:
    """Ricalcola gli embedding con il modello attivo, conservando persone e nomi.

    I marker perdono il cluster (face_id) ma non `subj_id` né `name`; alla fine i cluster del modello
    precedente, ormai inutilizzabili, vengono cancellati.
    """
    from mosaic.ai.analyze import face_engine_for

    spec = face_spec(ctx.models_dir)
    pending = _stale_markers(ctx.db.conn(), spec)
    if not pending:
        return ReembedStats()
    engine = engine or face_engine_for(ctx)
    by_photo: dict[int, list[sqlite3.Row]] = {}
    for m in pending:
        by_photo.setdefault(m["photo_id"], []).append(m)
    done = skipped = 0
    for index, (photo_id, markers) in enumerate(by_photo.items(), 1):
        if is_cancelled():
            return ReembedStats(done, skipped + sum(len(v) for v in list(by_photo.values())[index - 1:]))
        image = _marker_image(ctx, photo_id)
        if image is None:
            skipped += len(markers)
            continue
        vectors = engine.embed_rows(image, _yunet_rows(markers, image.shape[1], image.shape[0]))
        with ctx.db.tx() as conn:
            for m, vec in zip(markers, vectors):
                conn.execute("UPDATE markers SET embedding=?, embed_model=?, face_id=NULL, face_dist=-1, sugg_subj_id=NULL, sugg_dist=-1 WHERE id=?",
                             (vec.astype(np.float32).tobytes(), spec.recognizer.id, m["id"]))
        done += len(markers)
        if progress:
            progress(index / len(by_photo), f"{done}/{len(pending)} volti ricalcolati", done, len(pending))
    with ctx.db.tx() as conn:
        conn.execute("DELETE FROM faces WHERE embed_model!=?", (spec.recognizer.id,))
    log.info("volti ricalcolati con %s: %d fatti, %d saltati", spec.recognizer.id, done, skipped)
    return ReembedStats(done, skipped)


# --- passi ------------------------------------------------------------------------------

def prune_background(conn: sqlite3.Connection, spec: FaceSpec) -> int:
    """Toglie i volti automatici che non sono in primo piano (D-22). Quelli nominati o confermati a mano restano.

    Anche i volti a mano contano come riferimento: una persona vera fa da «volto principale» della foto.
    """
    rows = conn.execute(
        "SELECT m.id, m.photo_id, m.h, m.score, m.src, p.width, p.height FROM markers m JOIN photos p ON p.id=m.photo_id "
        "WHERE m.type='face' AND p.width>0 AND p.height>0 ORDER BY m.photo_id").fetchall()
    by_photo: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        by_photo.setdefault(r["photo_id"], []).append(r)
    drop: list[int] = []
    for faces in by_photo.values():
        width, height = faces[0]["width"], faces[0]["height"]
        keep = foreground(np.array([f["h"] * height for f in faces]), min(width, height),
                          np.array([f["score"] for f in faces]), spec)
        drop += [f["id"] for f, k in zip(faces, keep, strict=True) if not k and f["src"] < sources.MANUAL]
    for start in range(0, len(drop), 900):
        chunk = drop[start:start + 900]
        conn.execute(f"DELETE FROM markers WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    return len(drop)


def _step_orphans(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "DELETE FROM markers WHERE file_id IN (SELECT id FROM files WHERE missing=1) "
        "OR photo_id IN (SELECT id FROM photos WHERE deleted_at IS NOT NULL)")
    conn.execute("UPDATE markers SET face_id=NULL, face_dist=-1 WHERE face_id IS NOT NULL "
                 "AND face_id NOT IN (SELECT id FROM faces)")
    return cur.rowcount


def _step_names(conn: sqlite3.Connection) -> int:
    count = 0
    for row in conn.execute("SELECT id, name FROM markers WHERE name!='' AND subj_id IS NULL").fetchall():
        conn.execute("UPDATE markers SET subj_id=? WHERE id=?", (ensure_subject(conn, row["name"]), row["id"]))
        count += 1
    conn.execute("UPDATE markers SET name=(SELECT name FROM subjects WHERE id=markers.subj_id) "
                 "WHERE subj_id IS NOT NULL AND name=''")
    return count


def _step_collisions(conn: sqlite3.Connection) -> int:
    total = 0
    for face in conn.execute("SELECT * FROM faces").fetchall():
        rows = conn.execute("SELECT * FROM markers WHERE face_id=? AND subj_id IS NOT NULL", (face["id"],)).fetchall()
        subjects = {r["subj_id"] for r in rows}
        if len(subjects) < 2:
            continue
        main = face["subj_id"]
        if main is None:
            counts: dict[int, int] = {}
            for r in rows:
                counts[r["subj_id"]] = counts.get(r["subj_id"], 0) + 1
            main = max(counts, key=lambda k: counts[k])
        conflicting = [r for r in rows if r["subj_id"] != main]
        distances = [r["face_dist"] for r in conflicting if r["face_dist"] >= 0]
        for r in conflicting:
            _unassign(conn, r["id"])
        radius = face["collision_radius"]
        if distances:
            narrowed = min(distances) - EPSILON
            if narrowed >= cluster.COLLISION_DIST:
                radius = narrowed if radius <= 0 else min(radius, narrowed)
        conn.execute("UPDATE faces SET collisions=collisions+?, collision_radius=?, subj_id=COALESCE(subj_id, ?) WHERE id=?",
                     (len(conflicting), radius, main, face["id"]))
        total += len(conflicting)
    return total


def _step_merge(conn: sqlite3.Connection, spec: FaceSpec) -> int:
    merged = 0
    by_subject: dict[int, list[sqlite3.Row]] = {}
    for face in conn.execute("SELECT * FROM faces WHERE subj_id IS NOT NULL ORDER BY id").fetchall():
        by_subject.setdefault(face["subj_id"], []).append(face)
    for faces in by_subject.values():
        keep = list(faces)
        i = 0
        while i < len(keep):
            j = i + 1
            while j < len(keep):
                a, b = _vec(keep[i]["embedding"]), _vec(keep[j]["embedding"])
                if float(np.linalg.norm(a - b)) <= spec.recognizer.cluster_radius:
                    conn.execute("UPDATE markers SET face_id=? WHERE face_id=?", (keep[i]["id"], keep[j]["id"]))
                    conn.execute("DELETE FROM faces WHERE id=?", (keep[j]["id"],))
                    del keep[j]
                    merged += 1
                else:
                    j += 1
            _recompute_face(conn, keep[i]["id"], spec)
            i += 1
    return merged


def _step_match(conn: sqlite3.Connection, spec: FaceSpec) -> int:
    faces = _face_refs(conn, spec)
    if not faces:
        return 0
    by_id = {f.id: f for f in faces}
    matched = 0
    for row in _markers_with_embedding(conn, spec, "face_id IS NULL AND size>=?", (spec.size,)):
        hit = cluster.match_face(_vec(row["embedding"]), faces, spec.recognizer)
        if hit is None:
            continue
        face, dist = hit
        _assign(conn, row["id"], face.id, dist)
        if by_id[face.id].subj_id is not None and row["subj_id"] is None:
            conn.execute("UPDATE markers SET subj_id=?, name=(SELECT name FROM subjects WHERE id=?) WHERE id=?",
                         (by_id[face.id].subj_id, by_id[face.id].subj_id, row["id"]))
        matched += 1
    return matched


def _is_frontal(landmarks: str) -> bool:
    try:
        return frontal(json.loads(landmarks)) if landmarks else True
    except (ValueError, TypeError):
        return True


def _step_cluster(conn: sqlite3.Connection, spec: FaceSpec, core: int) -> int:
    rows = [r for r in _markers_with_embedding(
        conn, spec, "face_id IS NULL AND size>=? AND score>=?", (spec.cluster_size, spec.detector.cluster_score))
        if _is_frontal(r["landmarks"])]  # i volti di profilo si riconoscono, ma non fondano cluster
    if len(rows) < core:
        return 0
    vectors = np.stack([_vec(r["embedding"]) for r in rows])
    created = 0
    for group in cluster.cluster_faces(vectors, spec.recognizer, core):
        fid = cluster.face_id(group.centroid)
        conn.execute(
            "INSERT OR IGNORE INTO faces(id, samples, sample_radius, embed_model, embedding, created_at) VALUES (?,?,?,?,?,?)",
            (fid, len(group.members), group.radius, spec.recognizer.id, group.centroid.tobytes(), utcnow()))
        for idx in group.members:
            dist = float(np.linalg.norm(vectors[idx] - group.centroid))
            _assign(conn, rows[idx]["id"], fid, dist)
        created += 1
    return created


def _reference_vectors(conn: sqlite3.Connection, spec: FaceSpec) -> dict[int, np.ndarray]:
    """Vettori dei volti nominati dall'utente, per persona (i nomi automatici non fanno da riferimento)."""
    refs: dict[int, list[np.ndarray]] = {}
    for r in _markers_with_embedding(conn, spec, "subj_id IS NOT NULL AND src>=?", (sources.MANUAL,)):
        refs.setdefault(r["subj_id"], []).append(_vec(r["embedding"]))
    return {sid: np.stack(vs) for sid, vs in refs.items()}


def _subject_distance(vec: np.ndarray, refs: np.ndarray) -> float:
    """Distanza da una persona: media dei k volti nominati più vicini, k = min(3, n)."""
    dist = np.sort(np.linalg.norm(refs - vec, axis=1))
    return float(dist[:min(KNN, len(dist))].mean())


def _rejected_ids(text: str) -> set[int]:
    return {int(x) for x in text.split(",") if x.isdigit()}


def _step_subjects(conn: sqlite3.Connection, spec: FaceSpec) -> tuple[int, int]:
    """Riconosce i volti senza cluster e senza nome dalle persone già nominate (anche con un solo volto).

    Sotto `match_dist`, con vantaggio `match_margin` sulla seconda persona, il nome si assegna da solo;
    sotto `max_match_dist` resta come suggerimento da confermare. Ritorna (assegnati, suggeriti).
    """
    conn.execute("UPDATE markers SET sugg_subj_id=NULL, sugg_dist=-1 WHERE subj_id IS NULL")  # si ricalcolano da zero
    refs = _reference_vectors(conn, spec)
    if not refs:
        return 0, 0
    in_photo: dict[int, set[int]] = {}
    for r in conn.execute("SELECT photo_id, subj_id FROM markers WHERE invalid=0 AND subj_id IS NOT NULL"):
        in_photo.setdefault(r["photo_id"], set()).add(r["subj_id"])
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM subjects")}
    rec = spec.recognizer
    assigned = suggested = 0
    for row in _markers_with_embedding(conn, spec, "face_id IS NULL AND subj_id IS NULL AND size>=?", (spec.size,)):
        vec, rejected = _vec(row["embedding"]), _rejected_ids(row["sugg_rejected"])
        ranked = sorted(
            (_subject_distance(vec, v), sid) for sid, v in refs.items()
            if sid not in in_photo.get(row["photo_id"], set()))  # chi è già in questa foto non può comparirci due volte
        if not ranked or ranked[0][0] > rec.max_match_dist:
            continue
        dist, sid = ranked[0]
        clear = len(ranked) == 1 or ranked[1][0] - dist >= rec.match_margin
        if dist <= rec.match_dist and clear:
            conn.execute("UPDATE markers SET subj_id=?, name=?, sugg_dist=? WHERE id=?", (sid, names[sid], dist, row["id"]))
            in_photo.setdefault(row["photo_id"], set()).add(sid)
            assigned += 1
        elif sid not in rejected:
            conn.execute("UPDATE markers SET sugg_subj_id=?, sugg_dist=? WHERE id=?", (sid, dist, row["id"]))
            suggested += 1
    return assigned, suggested


def _step_unique_per_photo(conn: sqlite3.Connection) -> int:
    """La stessa persona compare una volta sola per foto: tiene un nome manuale, altrimenti il volto più vicino.

    Agli altri si toglie il nome automatico (e il cluster, perché il consenso non lo rimetta). I nomi manuali restano.
    """
    removed = 0
    groups: dict[tuple[int, int], list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT id, photo_id, subj_id, src, face_dist, sugg_dist FROM markers "
                          "WHERE invalid=0 AND subj_id IS NOT NULL ORDER BY id"):
        groups.setdefault((r["photo_id"], r["subj_id"]), []).append(r)

    def distance(r: sqlite3.Row) -> float:
        value = r["face_dist"] if r["face_dist"] >= 0 else r["sugg_dist"]
        return value if value >= 0 else float("inf")

    for rows in groups.values():
        if len(rows) < 2:
            continue
        manual = [r for r in rows if r["src"] >= sources.MANUAL]
        auto = sorted((r for r in rows if r["src"] < sources.MANUAL), key=distance)
        losers = auto if manual else auto[1:]
        for r in losers:
            conn.execute("UPDATE markers SET subj_id=NULL, name='', face_id=NULL, face_dist=-1, sugg_dist=-1 WHERE id=?", (r["id"],))
            removed += 1
    return removed


def _step_consensus(conn: sqlite3.Connection) -> int:
    named = 0
    for face in conn.execute("SELECT id FROM faces WHERE subj_id IS NULL").fetchall():
        subjects = {r["subj_id"] for r in conn.execute(
            "SELECT subj_id FROM markers WHERE face_id=? AND subj_id IS NOT NULL", (face["id"],))}
        if len(subjects) == 1:
            conn.execute("UPDATE faces SET subj_id=? WHERE id=?", (next(iter(subjects)), face["id"]))
            named += 1
    conn.execute(
        "UPDATE markers SET subj_id=(SELECT subj_id FROM faces WHERE id=markers.face_id), "
        "name=(SELECT s.name FROM faces f JOIN subjects s ON s.id=f.subj_id WHERE f.id=markers.face_id) "
        "WHERE subj_id IS NULL AND face_id IN (SELECT id FROM faces WHERE subj_id IS NOT NULL)")
    return named


def _step_cleanup(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE faces SET samples=(SELECT COUNT(*) FROM markers WHERE face_id=faces.id)")
    cur = conn.execute("DELETE FROM faces WHERE samples=0")
    return cur.rowcount


class FacesCancelled(Exception):
    """L'utente ha annullato: la transazione dei volti si annulla e i gruppi restano com'erano."""


def run_faces(ctx: Context, is_cancelled: Callable[[], bool] = lambda: False) -> FaceStats:
    """Esegue l'intero ciclo di riconoscimento sui marker con embedding. Annullabile tra un passo e l'altro."""
    spec = face_spec(ctx.models_dir)
    stats = FaceStats()

    def check() -> None:
        if is_cancelled():
            raise FacesCancelled

    with ctx.db.tx() as conn:
        stats.background = prune_background(conn, spec)
        stats.orphans = _step_orphans(conn)
        stats.named = _step_names(conn)
        stats.collisions = _step_collisions(conn)
        check()
        stats.merged = _step_merge(conn, spec)
        stats.matched = _step_match(conn, spec)
        check()
        stats.clusters = _step_cluster(conn, spec, spec.cluster_core)
        stats.matched += _step_match(conn, spec)
        check()
        stats.retry_clusters = _step_cluster(conn, spec, spec.cluster_core_retry)
        stats.matched += _step_match(conn, spec)
        check()
        stats.subjects, stats.suggested = _step_subjects(conn, spec)
        stats.deduplicated = _step_unique_per_photo(conn)
        stats.consensus = _step_consensus(conn)
        stats.removed = _step_cleanup(conn)
        update_counts(conn)
    log.info("volti: %s", stats)
    return stats
