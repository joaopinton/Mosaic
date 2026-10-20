"""«Crea selezione» (D-21): le foto che riassumono un album o una cartella.

Si scelgono foto venute bene (nitide, ben esposte, belle secondo SigLIP), il più possibile diverse tra loro
(niente raffiche né quasi-doppioni), distribuite lungo tutto l'evento e con più persone possibile.
La scelta è golosa, come la Maximal Marginal Relevance: a ogni passo vince la foto con il punteggio
`qualità + momento non coperto + persone nuove − somiglianza con le già scelte`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from mosaic.context import Context
from mosaic.organize import aesthetics
from mosaic.search import query as search
from mosaic.search.parser import parse

log = logging.getLogger(__name__)

MAX_COUNT = 200
SHAPES = ("landscape", "portrait", "square")
RATIO_RE = re.compile(r"^\d{1,2}(\.\d{1,2})?:\d{1,2}(\.\d{1,2})?$")
NEAR_DUP_COS = 0.92         # coseno SigLIP oltre il quale due foto sono lo stesso scatto (raffica)
NEAR_DUP_PHASH = 6          # bit diversi del phash sotto i quali, senza embedding, sono lo stesso scatto
BURST_SECONDS = 10          # senza embedding: foto così vicine nel tempo contano come simili
SIM_FLOOR = 0.55            # sotto questo coseno due foto sono già diverse: nessuna penalità
SHARP_LOW, SHARP_HIGH = 3.5, 6.5  # log-varianza del Laplaciano: sotto è mossa o sfocata, sopra è nitida
BAD_SHARP, BAD_EXPOSURE = 0.2, 0.35
PROMINENT_FACE = 0.04       # un volto che copre il 4% dell'inquadratura è già un protagonista
JITTER = 0.12               # variazione casuale di «Rigenera»
TIMELINE_BINS = 48
GOOD_PROMPTS = ("una bella fotografia nitida e ben composta", "a beautiful, sharp, well composed photograph")
BAD_PROMPTS = ("una foto sfocata, mossa o buia", "a blurry, out of focus, badly exposed photo",
               "a screenshot or a photo of a document")


class CurateError(ValueError):
    """Opzioni non valide."""


@dataclass(frozen=True)
class Weights:
    quality: float
    diversity: float
    time: float
    people: float


MODES = {
    "quality": Weights(1.0, 0.45, 0.25, 0.30),
    "balanced": Weights(0.75, 0.75, 0.35, 0.40),
    "variety": Weights(0.5, 1.0, 0.45, 0.50),
}


@dataclass(frozen=True)
class Options:
    count: int = 24
    shape: str = ""      # "", landscape, portrait, square oppure un formato come 4:3
    text: str = ""       # cosa deve esserci: testo libero o filtri, come nella ricerca
    mode: str = "balanced"
    people: bool = True
    videos: bool = False
    seed: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.count <= MAX_COUNT:
            raise CurateError(f"il numero di foto va da 1 a {MAX_COUNT}")
        if self.mode not in MODES:
            raise CurateError(f"modo sconosciuto: {self.mode}")
        if self.shape and self.shape not in SHAPES and not RATIO_RE.match(self.shape):
            raise CurateError(f"formato non valido: {self.shape}")


@dataclass
class Pick:
    id: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Result:
    picks: list[Pick]
    stats: dict[str, Any]


def filter_query(album_uid: str, opts: Options) -> str:
    """Query di ricerca delle candidate: la stessa che il pannello usa per contarle."""
    parts = [f"album:{album_uid}"]
    if opts.shape in SHAPES:
        parts.append(f"{opts.shape}:true")
    elif opts.shape:
        parts.append(f"ratio:{opts.shape}")
    if not opts.videos:
        parts.append("photo:true")
    if opts.text.strip():
        parts.append(opts.text.strip())
    return " ".join(parts)


# --- candidate -------------------------------------------------------------------------------------

@dataclass
class Pool:
    rows: list[dict[str, Any]]
    relevance: np.ndarray | None
    seconds: np.ndarray                  # istante di scatto (NaN se manca)
    people: list[frozenset[str]]         # "s<id>" persona con nome, "f<id>" gruppo di volti senza nome
    names: dict[str, str]
    face_area: np.ndarray
    vectors: np.ndarray                  # righe a zero dove manca l'embedding
    has_vec: np.ndarray

    def __len__(self) -> int:
        return len(self.rows)


def _candidate_ids(ctx: Context, q: str) -> tuple[list[int], np.ndarray | None]:
    parsed = parse(q)
    conn = ctx.db.conn()
    where, params = search.build_where(conn, parsed)
    ids = [r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where} ORDER BY p.id", params)]
    if not parsed.text or not ids:
        return ids, None
    ordered, _ = search.rank_text(ctx, parsed.text, set(ids), len(ids))
    relevance = 1 - np.arange(len(ordered), dtype=np.float32) / max(len(ordered), 1)
    return ordered, relevance


def _seconds(value: str | None) -> float:
    if not value:
        return float("nan")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("nan")


def _people(ctx: Context, ids: list[int]) -> tuple[dict[int, set[str]], dict[int, float], dict[str, str]]:
    found: dict[int, set[str]] = {}
    area: dict[int, float] = {}
    names: dict[str, str] = {}
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        rows = ctx.db.all(
            f"SELECT m.photo_id, m.subj_id, m.face_id, m.w, m.h, s.name, s.hidden FROM markers m "
            f"LEFT JOIN subjects s ON s.id=m.subj_id WHERE m.invalid=0 AND m.type='face' "
            f"AND m.photo_id IN ({','.join('?' * len(chunk))})", tuple(chunk))
        for r in rows:
            area[r["photo_id"]] = max(area.get(r["photo_id"], 0.0), float(r["w"] * r["h"]))
            if r["subj_id"] is not None and not r["hidden"]:
                key = f"s{r['subj_id']}"
                names[key] = r["name"]
            elif r["face_id"]:
                key = f"f{r['face_id']}"
            else:
                continue
            found.setdefault(r["photo_id"], set()).add(key)
    return found, area, names


def load_pool(ctx: Context, q: str) -> Pool:
    from mosaic.ai.vectors import get_index

    ids, relevance = _candidate_ids(ctx, q)
    rows = [r for s in range(0, len(ids), 900) for r in search.photo_summaries(ctx, ids[s:s + 900])]
    extra = {r["id"]: r for r in _extra(ctx, ids)}
    rows = [{**r, **extra.get(r["id"], {})} for r in rows]
    people, area, names = _people(ctx, ids)
    index = get_index(ctx)
    dim = index.matrix.shape[1] if len(index) else 1
    vectors = np.zeros((len(rows), dim), dtype=np.float32)
    has_vec = np.zeros(len(rows), dtype=bool)
    for n, r in enumerate(rows):
        vec = index.vector(r["id"]) if len(index) else None
        if vec is not None:
            vectors[n], has_vec[n] = vec, True
    return Pool(rows=rows, relevance=relevance if relevance is None else relevance[:len(rows)],
                seconds=np.array([_seconds(r.get("taken_at")) for r in rows], dtype=np.float64),
                people=[frozenset(people.get(r["id"], ())) for r in rows], names=names,
                face_area=np.array([area.get(r["id"], 0.0) for r in rows], dtype=np.float32),
                vectors=vectors, has_vec=has_vec)


def _extra(ctx: Context, ids: list[int]) -> list[Any]:
    out: list[Any] = []
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        out += [dict(r) for r in ctx.db.all(
            f"SELECT id, taken_at, rating, phash FROM photos WHERE id IN ({','.join('?' * len(chunk))})", tuple(chunk))]
    return out


# --- qualità ----------------------------------------------------------------------------------------

def rank01(values: np.ndarray) -> np.ndarray:
    """Posizione in classifica, da 0 (peggiore) a 1 (migliore); NaN restano NaN."""
    out = np.full(values.shape, np.nan, dtype=np.float32)
    ok = ~np.isnan(values)
    n = int(ok.sum())
    if n == 1:
        out[ok] = 1.0
    elif n > 1:
        order = np.argsort(np.argsort(values[ok], kind="stable"), kind="stable")
        out[ok] = order / (n - 1)
    return out


def _aesthetic(ctx: Context, pool: Pool) -> np.ndarray | None:
    if not pool.has_vec.any():
        return None
    try:
        good = [search.text_embedding(ctx, t) for t in GOOD_PROMPTS]
        bad = [search.text_embedding(ctx, t) for t in BAD_PROMPTS]
    except Exception as exc:  # modello del testo assente o rotto: si sceglie senza estetica
        log.warning("estetica SigLIP non disponibile: %s", exc)
        return None
    if any(v is None or v.shape[-1] != pool.vectors.shape[1] for v in good + bad):
        return None  # modello assente o embedding di un altro modello
    raw = pool.vectors @ np.mean(good, axis=0) - pool.vectors @ np.mean(bad, axis=0)
    raw = np.where(pool.has_vec, raw, np.nan)
    return rank01(raw.astype(np.float64))


def quality(ctx: Context, pool: Pool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Qualità 0..1 di ogni candidata, più nitidezza assoluta ed esposizione (per scartare le venute male)."""
    m = aesthetics.measures(ctx, [(r["id"], r.get("hash")) for r in pool.rows])
    raw = np.array([m[r["id"]].sharpness if r["id"] in m else np.nan for r in pool.rows], dtype=np.float64)
    sharp_abs = np.clip((raw - SHARP_LOW) / (SHARP_HIGH - SHARP_LOW), 0, 1)
    sharp = np.nan_to_num(0.5 * sharp_abs + 0.5 * rank01(raw), nan=0.5)
    exposure = np.array([m[r["id"]].exposure if r["id"] in m else 0.7 for r in pool.rows], dtype=np.float32)
    mp = np.array([(r["width"] or 0) * (r["height"] or 0) / 1e6 for r in pool.rows])
    meta = np.clip(0.35 + 0.35 * np.array([bool(r["favorite"]) for r in pool.rows])
                   + 0.15 * np.array([(r.get("rating") or 0) >= 4 for r in pool.rows])
                   + 0.25 * np.clip(pool.face_area / PROMINENT_FACE, 0, 1) - 0.25 * (mp < 2), 0, 1)
    aest = _aesthetic(ctx, pool)
    if aest is None:
        q = (0.45 * sharp + 0.30 * exposure + 0.25 * meta)
    else:
        q = 0.40 * sharp + 0.20 * exposure + 0.25 * np.nan_to_num(aest, nan=0.5) + 0.15 * meta
    if pool.relevance is not None:
        q = 0.75 * q + 0.25 * pool.relevance
    return q.astype(np.float32), np.nan_to_num(sharp_abs, nan=0.5), exposure


# --- scelta ---------------------------------------------------------------------------------------

def _phash_bits(pool: Pool) -> tuple[np.ndarray, np.ndarray]:
    values = [r.get("phash") for r in pool.rows]
    has = np.array([v is not None for v in values], dtype=bool)
    bits = np.array([(v or 0) & 0xFFFFFFFFFFFFFFFF for v in values], dtype=np.uint64)
    return bits, has


def _similarity_row(pool: Pool, i: int, phash: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Somiglianza di tutte le candidate con la `i`: coseno SigLIP, altrimenti phash e vicinanza nel tempo."""
    sim = np.zeros(len(pool), dtype=np.float32)
    gap = np.abs(pool.seconds - pool.seconds[i])
    sim[np.nan_to_num(gap, nan=np.inf) <= BURST_SECONDS] = 0.75
    bits, has = phash
    if has[i]:
        diff = np.unpackbits((bits ^ bits[i]).view(np.uint8).reshape(-1, 8), axis=1).sum(axis=1)
        sim[has & (diff <= NEAR_DUP_PHASH)] = 0.95
    if pool.has_vec[i]:
        cos = pool.vectors @ pool.vectors[i]
        sim = np.where(pool.has_vec, np.maximum(cos, np.where(sim >= 0.95, sim, 0)), sim)
    return sim


def _segments(pool: Pool, k: int) -> np.ndarray:
    """Divide l'evento in `k` fasce con lo stesso numero di foto, in ordine di scatto."""
    n = len(pool)
    order = sorted(range(n), key=lambda j: (np.isnan(pool.seconds[j]), np.nan_to_num(pool.seconds[j]), pool.rows[j]["id"]))
    seg = np.empty(n, dtype=np.int64)
    seg[order] = np.arange(n) * k // n
    return seg


@dataclass
class _State:
    taken: np.ndarray
    blocked: np.ndarray
    maxsim: np.ndarray
    covered: np.ndarray
    seen: dict[str, int]
    bonus: np.ndarray


def _people_bonus(people: frozenset[str], seen: dict[str, int]) -> float:
    """0..1: alto se nella foto c'è chi non è ancora comparso; chi è già comparso conta sempre meno."""
    fresh = sum(1 / (1 + seen.get(p, 0)) ** 2 for p in people)
    return float(1 - np.exp(-1.5 * fresh))


def select(pool: Pool, score: np.ndarray, opts: Options) -> list[Pick]:
    """Scelta golosa: qualità + fascia di tempo nuova + persone nuove − somiglianza; niente quasi-doppioni."""
    n = len(pool)
    k = min(opts.count, n)
    if k == 0:
        return []
    w = MODES[opts.mode]
    seg = _segments(pool, k)
    by_person: dict[str, list[int]] = {}
    for j, people in enumerate(pool.people):
        for p in people:
            by_person.setdefault(p, []).append(j)
    st = _State(taken=np.zeros(n, bool), blocked=np.zeros(n, bool), maxsim=np.zeros(n, np.float32),
                covered=np.zeros(k, np.float32), seen={},
                bonus=np.array([_people_bonus(p, {}) for p in pool.people], dtype=np.float32))
    jitter = np.random.default_rng(opts.seed).uniform(0, JITTER, n) if opts.seed else np.zeros(n)
    phash = _phash_bits(pool)
    picks: list[Pick] = []
    for _ in range(k):
        penalty = np.clip((st.maxsim - SIM_FLOOR) / (1 - SIM_FLOOR), 0, 1)
        total = (w.quality * score + w.time / (1 + st.covered[seg]) + (w.people * st.bonus if opts.people else 0)
                 - w.diversity * penalty + jitter - 100.0 * st.blocked)
        total[st.taken] = -np.inf
        i = int(np.argmax(total))
        if not np.isfinite(total[i]) or st.blocked[i]:
            break  # restano solo raffiche e quasi-doppioni: meglio meno foto che foto ripetute
        picks.append(_take(pool, i, st, seg, by_person, phash))
    return picks


def _take(pool: Pool, i: int, st: _State, seg: np.ndarray, by_person: dict[str, list[int]],
          phash: tuple[np.ndarray, np.ndarray]) -> Pick:
    reasons: list[str] = []
    if pool.rows[i]["favorite"]:
        reasons.append("Preferita")
    new = [p for p in pool.people[i] if st.seen.get(p, 0) == 0]
    named = sorted(pool.names[p] for p in new if p in pool.names)
    if named:
        reasons.append("Prima foto con " + _join(named))
    elif new:
        reasons.append("Un volto nuovo" if len(new) == 1 else f"{len(new)} volti nuovi")
    sim = _similarity_row(pool, i, phash)
    twins = int(((sim >= NEAR_DUP_COS) & ~st.taken & ~st.blocked).sum()) - 1
    if twins > 0:
        reasons.append(f"La migliore di {twins + 1} scatti simili")
    if st.covered[seg[i]] == 0 and not reasons:
        reasons.append("Un momento non ancora coperto")
    st.taken[i] = True
    st.blocked |= sim >= NEAR_DUP_COS
    st.maxsim = np.maximum(st.maxsim, sim)
    st.covered[seg[i]] += 1
    for p in pool.people[i]:
        st.seen[p] = st.seen.get(p, 0) + 1
        for j in by_person[p]:
            st.bonus[j] = _people_bonus(pool.people[j], st.seen)
    return Pick(pool.rows[i]["id"], reasons)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


# --- insieme --------------------------------------------------------------------------------------

def curate(ctx: Context, album_uid: str, opts: Options) -> Result:
    pool = load_pool(ctx, filter_query(album_uid, opts))
    if len(pool) == 0:
        return Result([], _stats(pool, [], 0, 0))
    score, sharp_abs, exposure = quality(ctx, pool)
    bad = (sharp_abs < BAD_SHARP) | (exposure < BAD_EXPOSURE)
    rejected = 0
    if int((~bad).sum()) >= max(opts.count, int(opts.count * 1.5)):
        score = np.where(bad, -np.inf, score)  # le venute male restano fuori, se ce ne sono abbastanza di buone
        rejected = int(bad.sum())
    usable = np.isfinite(score)
    picks = select(_subset(pool, usable), score[usable], opts)
    taken = {p.id for p in picks}
    order = {r["id"]: (np.isnan(pool.seconds[j]), np.nan_to_num(pool.seconds[j]), r["id"]) for j, r in enumerate(pool.rows)}
    picks.sort(key=lambda p: order[p.id])
    near = len(pool) - rejected - len(taken)
    return Result(picks, _stats(pool, picks, rejected, near))


def _subset(pool: Pool, mask: np.ndarray) -> Pool:
    idx = np.nonzero(mask)[0]
    return Pool(rows=[pool.rows[j] for j in idx], relevance=None if pool.relevance is None else pool.relevance[idx],
                seconds=pool.seconds[idx], people=[pool.people[j] for j in idx], names=pool.names,
                face_area=pool.face_area[idx], vectors=pool.vectors[idx], has_vec=pool.has_vec[idx])


def _stats(pool: Pool, picks: list[Pick], rejected: int, others: int) -> dict[str, Any]:
    taken = {p.id for p in picks}
    in_pick = [j for j, r in enumerate(pool.rows) if r["id"] in taken]
    everyone = set().union(*pool.people) if pool.people else set()
    covered: dict[str, int] = {}
    for j in in_pick:
        for p in pool.people[j]:
            covered[p] = covered.get(p, 0) + 1
    named = sorted(((pool.names[p], c) for p, c in covered.items() if p in pool.names), key=lambda x: (-x[1], x[0]))
    dates = [r["taken_at_local"] for r in pool.rows if r.get("taken_at_local")]
    return {
        "candidates": len(pool), "selected": len(picks), "rejected": rejected, "not_chosen": max(others, 0),
        "people_total": len(everyone), "people_covered": len(covered),
        "people": [{"name": name, "count": count} for name, count in named],
        "start": min(dates) if dates else None, "end": max(dates) if dates else None,
        "timeline": _timeline(pool, set(in_pick)),
        "ai_ready": bool(len(pool)) and float(pool.has_vec.mean()) >= 0.8,
    }


def _timeline(pool: Pool, picked: set[int]) -> list[list[int]]:
    """Istogramma dell'evento: per ogni fascia [foto candidate, foto scelte]."""
    known = [j for j in range(len(pool)) if not np.isnan(pool.seconds[j])]
    if len(known) < 2:
        return []
    times = np.array([pool.seconds[j] for j in known])
    # estremi robusti: una foto con la data sbagliata non deve schiacciare tutto l'evento in una fascia
    low, high = (np.percentile(times, [2, 98]) if len(known) >= 20 else (times.min(), times.max()))
    bins = [[0, 0] for _ in range(TIMELINE_BINS)]
    span = max(high - low, 1.0)
    for j in known:
        b = min(max(int((pool.seconds[j] - low) / span * TIMELINE_BINS), 0), TIMELINE_BINS - 1)
        bins[b][0] += 1
        bins[b][1] += j in picked
    return bins
