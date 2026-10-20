"""Foto simili con punteggio e spiegazione (evoluzione di 05_similarity_engine.py del vecchio Mosaic).

score = 0,60·coseno(SigLIP) + 0,15·jaccard(etichette) + 0,10·jaccard(persone) + 0,10·vicinanza temporale + 0,05·colori
Le spiegazioni sono distribuite su quattro assi: soggetto, inquadratura, palette, contesto.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from mosaic.ai.vectors import get_index
from mosaic.context import Context

W_COSINE, W_LABELS, W_PEOPLE, W_TIME, W_COLORS = 0.60, 0.15, 0.10, 0.10, 0.05
TIME_SCALE_DAYS = 3.0
CANDIDATE_FACTOR = 4
AXES = ("soggetto", "inquadratura", "palette", "contesto")


@dataclass(frozen=True)
class SimilarHit:
    photo_id: int
    score: float
    cosine: float
    reasons: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Profile:
    id: int
    labels: frozenset[str]
    people: frozenset[str]
    colors: frozenset[str]
    taken: datetime | None
    width: int
    height: int
    camera_id: int | None
    folder_id: int | None


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _profiles(conn: sqlite3.Connection, ids: list[int]) -> dict[int, _Profile]:
    marks = ",".join("?" * len(ids))
    photos = conn.execute(
        f"SELECT id, taken_at_local, width, height, camera_id, folder_id, colors FROM photos WHERE id IN ({marks})", ids).fetchall()
    labels: dict[int, set[str]] = {}
    for r in conn.execute(
        f"SELECT pl.photo_id, l.name FROM photos_labels pl JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id IN ({marks})", ids):
        labels.setdefault(r[0], set()).add(r[1])
    people: dict[int, set[str]] = {}
    for r in conn.execute(
        f"SELECT m.photo_id, s.name FROM markers m JOIN subjects s ON s.id=m.subj_id WHERE m.invalid=0 AND m.photo_id IN ({marks})", ids):
        people.setdefault(r[0], set()).add(r[1])
    return {
        p["id"]: _Profile(p["id"], frozenset(labels.get(p["id"], ())), frozenset(people.get(p["id"], ())),
                          frozenset((p["colors"] or "").split()), _parse(p["taken_at_local"]), p["width"], p["height"],
                          p["camera_id"], p["folder_id"])
        for p in photos
    }


def time_closeness(a: datetime | None, b: datetime | None) -> float:
    if a is None or b is None:
        return 0.0
    return math.exp(-abs((a - b).total_seconds()) / 86400 / TIME_SCALE_DAYS)


def _orientation(p: _Profile) -> str:
    return "orizzontale" if p.width > p.height else "verticale" if p.height > p.width else "quadrata"


def explain(base: _Profile, other: _Profile, cosine: float) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    subject: list[str] = []
    if cosine >= 0.85:
        subject.append("soggetto molto simile")
    elif cosine >= 0.70:
        subject.append("soggetto simile")
    shared = sorted(base.labels & other.labels)
    if shared:
        subject.append("stesse etichette: " + ", ".join(shared[:4]).lower())
    if subject:
        reasons["soggetto"] = subject

    framing: list[str] = []
    if base.width and other.width and _orientation(base) == _orientation(other):
        ratio = (base.width / base.height) / (other.width / other.height)
        framing.append(f"stessa inquadratura {_orientation(base)}" if abs(ratio - 1) < 0.1
                       else f"stessa orientazione {_orientation(base)}")
    if base.camera_id is not None and base.camera_id == other.camera_id:
        framing.append("stessa fotocamera")
    if framing:
        reasons["inquadratura"] = framing

    colors = sorted(base.colors & other.colors)
    if colors:
        reasons["palette"] = ["palette in comune: " + ", ".join(c.lower() for c in colors)]

    context: list[str] = []
    if base.taken and other.taken:
        delta = abs((base.taken - other.taken).total_seconds())
        if delta <= 3600:
            context.append("scattata nello stesso momento")
        elif base.taken.date() == other.taken.date():
            context.append("scattata lo stesso giorno")
    people = sorted(base.people & other.people)
    if people:
        context.append("stesse persone: " + ", ".join(people[:3]))
    if base.folder_id is not None and base.folder_id == other.folder_id:
        context.append("nella stessa cartella")
    if context:
        reasons["contesto"] = context
    return reasons


def similar_photos(ctx: Context, photo_id: int, k: int = 12, include_private: bool = False) -> list[SimilarHit]:
    """Le k foto più simili (escluso l'archivio). Ritorna [] se la foto non ha ancora un embedding."""
    index = get_index(ctx)
    vec = index.vector(photo_id)
    if vec is None:
        return []
    ids, cosines = index.search(vec, k * CANDIDATE_FACTOR + 1)
    cos_by_id = {int(i): float(c) for i, c in zip(ids, cosines, strict=True) if int(i) != photo_id}
    if not cos_by_id:
        return []
    conn = ctx.db.conn()
    wanted = [photo_id, *cos_by_id]
    profiles = _profiles(conn, wanted)
    base = profiles.get(photo_id)
    if base is None:
        return []
    visible = {r[0] for r in conn.execute(
        f"SELECT id FROM photos WHERE id IN ({','.join('?' * len(wanted))}) AND deleted_at IS NULL"
        + ("" if include_private else " AND private=0"), wanted)}
    hits: list[SimilarHit] = []
    for pid, cosine in cos_by_id.items():
        other = profiles.get(pid)
        if other is None or pid not in visible:
            continue
        score = (W_COSINE * max(0.0, min(1.0, cosine)) + W_LABELS * jaccard(base.labels, other.labels)
                 + W_PEOPLE * jaccard(base.people, other.people) + W_TIME * time_closeness(base.taken, other.taken)
                 + W_COLORS * jaccard(base.colors, other.colors))
        hits.append(SimilarHit(pid, round(score, 4), round(cosine, 4), explain(base, other, cosine)))
    hits.sort(key=lambda h: (-h.score, h.photo_id))
    return hits[:k]
