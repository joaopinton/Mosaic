"""Ricerca: filtri SQL + testo libero ibrido (FTS5 e similarità semantica SigLIP), con ordinamento e paginazione."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mosaic.context import Context
from mosaic.search import fts
from mosaic.search.parser import Filter, Query, QueryError, parse, to_bool, to_date, to_range, to_ratio

log = logging.getLogger(__name__)

MAX_ALBUM_DEPTH = 3
SEMANTIC_FLOOR = 0.03      # coseno minimo testo→immagine per entrare nei risultati
SEMANTIC_BAND = 0.05       # entrano i risultati entro questa distanza dal migliore
SEMANTIC_WEIGHT, FTS_WEIGHT = 0.7, 0.3
DEFAULT_COUNT, MAX_COUNT = 60, 500
ORDERS = {"taken", "added", "name", "quality", "random", "relevance", "oldest", "newest"}
REVIEW_BELOW = 3
SHAPE_TOLERANCE = 0.06     # formato e dimensioni singole: ±6% (le foto non hanno mai proporzioni esatte)
HAS_SIZE = "p.width>0 AND p.height>0"


@dataclass
class SearchResult:
    ids: list[int]
    total: int
    semantic: bool = False
    photos: list[dict[str, Any]] = field(default_factory=list)


def like(value: str) -> str:
    """Valore per LIKE con escape dei caratteri speciali."""
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def range_sql(column: str, low: float | None, high: float | None) -> tuple[str, list[Any]]:
    """Condizione SQL per un intervallo aperto o chiuso su `column`."""
    if low is not None and high is not None:
        return f"{column} BETWEEN ? AND ?", [low, high]
    return (f"{column}>=?", [low]) if low is not None else (f"{column}<=?", [high])


def prefix_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class _Builder:
    def __init__(self, conn: sqlite3.Connection, depth: int = 0) -> None:
        self.conn = conn
        self.depth = depth
        self.clauses: list[str] = []
        self.params: list[Any] = []

    def add(self, sql: str, *params: Any, negate: bool = False) -> None:
        self.clauses.append(f"NOT ({sql})" if negate else f"({sql})")
        self.params.extend(params)

    def any_of(self, f: Filter, make) -> None:  # type: ignore[no-untyped-def]
        """Applica `make(valore) -> (sql, params)` a ogni valore, in OR (e NOT se negato)."""
        parts, params = [], []
        for value in f.values:
            sql, p = make(value)
            parts.append(sql)
            params.extend(p)
        self.add(" OR ".join(parts), *params, negate=f.negate)

    # --- filtri ---------------------------------------------------------------------
    def apply(self, f: Filter) -> None:
        handler = getattr(self, f"_f_{f.key}", None)
        if handler is not None:
            handler(f)

    def _bool(self, f: Filter, sql: str, inverse: str | None = None) -> None:
        value = to_bool(f.values[0]) != f.negate
        self.add(sql if value else (inverse or f"NOT ({sql})"))

    def _f_favorite(self, f: Filter) -> None: self._bool(f, "p.favorite=1")
    def _f_private(self, f: Filter) -> None: self._bool(f, "p.private=1")
    def _f_nsfw(self, f: Filter) -> None: self._bool(f, "p.nsfw=1")
    def _f_archived(self, f: Filter) -> None: self._bool(f, "p.deleted_at IS NOT NULL")
    def _f_review(self, f: Filter) -> None: self._bool(f, f"p.quality<{REVIEW_BELOW}")
    def _f_live(self, f: Filter) -> None: self._bool(f, "p.type='live'")
    def _f_video(self, f: Filter) -> None: self._bool(f, "p.type='video'")
    def _f_animated(self, f: Filter) -> None: self._bool(f, "p.type='animated'")
    def _f_raw(self, f: Filter) -> None: self._bool(f, "EXISTS (SELECT 1 FROM files fr WHERE fr.photo_id=p.id AND fr.media_type='raw')")
    def _f_photo(self, f: Filter) -> None: self._bool(f, "p.type IN ('image','raw','live','animated')")
    def _f_geo(self, f: Filter) -> None: self._bool(f, "p.lat IS NOT NULL AND p.lng IS NOT NULL")
    def _f_portrait(self, f: Filter) -> None: self._bool(f, "p.height>p.width")
    def _f_landscape(self, f: Filter) -> None: self._bool(f, "p.width>p.height")
    def _f_square(self, f: Filter) -> None: self._bool(f, "p.width=p.height AND p.width>0")
    def _f_faces(self, f: Filter) -> None: self._bool(f, "EXISTS (SELECT 1 FROM markers m WHERE m.photo_id=p.id AND m.invalid=0)")
    def _f_missing(self, f: Filter) -> None: self._bool(f, "EXISTS (SELECT 1 FROM files fm WHERE fm.photo_id=p.id AND fm.missing=1)")
    def _f_duplicate(self, f: Filter) -> None: self._bool(f, "EXISTS (SELECT 1 FROM duplicates d JOIN files fd ON fd.id=d.of_file_id WHERE fd.photo_id=p.id)")

    def _f_type(self, f: Filter) -> None:
        self.any_of(f, lambda v: ("p.type=?", [v.lower()]))

    def _f_label(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.id IN (SELECT pl.photo_id FROM photos_labels pl JOIN labels l ON l.id=pl.label_id "
            "WHERE l.slug=? OR lower(l.name)=lower(?))", [v.lower(), v]))

    def _f_category(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.id IN (SELECT pl.photo_id FROM photos_labels pl JOIN labels l ON l.id=pl.label_id "
            "WHERE lower(l.category)=lower(?))", [v]))

    def _f_keyword(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.id IN (SELECT pk.photo_id FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id "
            "WHERE k.word LIKE ? ESCAPE '\\')", [like(v.lower())]))

    def _f_person(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.id IN (SELECT m.photo_id FROM markers m JOIN subjects s ON s.id=m.subj_id WHERE m.invalid=0 "
            "AND (s.uid=? OR s.slug=? OR lower(s.name) LIKE ? ESCAPE '\\'))", [v, v.lower(), like(v.lower())]))

    def _f_face(self, f: Filter) -> None:
        self.any_of(f, lambda v: ("p.id IN (SELECT m.photo_id FROM markers m WHERE m.invalid=0 AND m.face_id=?)", [v]))

    def _f_album(self, f: Filter) -> None:
        self.any_of(f, self._album_clause)

    def _album_clause(self, value: str) -> tuple[str, list[Any]]:
        row = self.conn.execute(
            "SELECT id, filter FROM albums WHERE deleted_at IS NULL AND (uid=? OR slug=? OR lower(title)=lower(?)) LIMIT 1",
            (value, value.lower(), value)).fetchone()
        if row is None:
            return "0", []
        if row["filter"] and self.depth < MAX_ALBUM_DEPTH:  # smart album: la sua query diventa una sotto-query
            sub = _Builder(self.conn, self.depth + 1)
            sub.build(parse(row["filter"]), default_visibility=False)
            return f"p.id IN (SELECT p.id FROM photos p WHERE {sub.where()})", sub.params
        return "p.id IN (SELECT photo_id FROM photos_albums WHERE album_id=?)", [row["id"]]

    def _f_folder(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.folder_id IN (SELECT id FROM folders WHERE path=? OR path LIKE ? ESCAPE '\\')",
            [v.strip("/"), prefix_like(v.strip("/") + "/")]))

    def _f_root(self, f: Filter) -> None:
        """Tutte le foto di una cartella data alla libreria, sottocartelle comprese (`root` come in config.yaml)."""
        self.any_of(f, lambda v: ("p.folder_id IN (SELECT id FROM folders WHERE root=?)", [v]))

    def _date_part(self, f: Filter, fmt: str) -> None:
        def make(v: str) -> tuple[str, list[Any]]:
            low, high = to_range(v)
            expr = f"CAST(strftime('{fmt}', p.taken_at_local) AS INTEGER)"
            if low is not None and high is not None:
                return f"{expr} BETWEEN ? AND ?", [int(low), int(high)]
            return (f"{expr}>=?", [int(low)]) if low is not None else (f"{expr}<=?", [int(high or 0)])
        self.any_of(f, make)

    def _f_year(self, f: Filter) -> None: self._date_part(f, "%Y")
    def _f_month(self, f: Filter) -> None: self._date_part(f, "%m")
    def _f_day(self, f: Filter) -> None: self._date_part(f, "%d")
    def _f_after(self, f: Filter) -> None: self.add("p.taken_at_local>=?", to_date(f.values[0]), negate=f.negate)
    def _f_before(self, f: Filter) -> None: self.add("p.taken_at_local<?", to_date(f.values[0]), negate=f.negate)

    def _numeric(self, f: Filter, column: str, approx: bool = False, guard: str = "") -> None:
        """Intervallo su una colonna. Con `approx` un valore singolo vale ±SHAPE_TOLERANCE; `guard` esclude le righe senza dato."""
        def make(v: str) -> tuple[str, list[Any]]:
            low, high = to_range(v)
            if approx and low is not None and low == high:
                low, high = low * (1 - SHAPE_TOLERANCE), high * (1 + SHAPE_TOLERANCE)
            sql, params = range_sql(column, low, high)
            return (f"({guard} AND {sql})" if guard else sql), params
        self.any_of(f, make)

    def _f_ratio(self, f: Filter) -> None:
        def make(v: str) -> tuple[str, list[Any]]:
            target = to_ratio(v)
            return (f"({HAS_SIZE} AND CAST(p.width AS REAL) / p.height BETWEEN ? AND ?)",
                    [target * (1 - SHAPE_TOLERANCE), target * (1 + SHAPE_TOLERANCE)])
        self.any_of(f, make)

    def _f_mp(self, f: Filter) -> None: self._numeric(f, "(CAST(p.width AS REAL) * p.height / 1000000.0)", True, HAS_SIZE)
    def _f_width(self, f: Filter) -> None: self._numeric(f, "p.width", True, HAS_SIZE)
    def _f_height(self, f: Filter) -> None: self._numeric(f, "p.height", True, HAS_SIZE)

    def _f_mb(self, f: Filter) -> None:
        size = "(SELECT size / 1048576.0 FROM files fs WHERE fs.photo_id=p.id AND fs.is_primary=1)"
        self._numeric(f, size, True, f"{size} IS NOT NULL")

    def _f_iso(self, f: Filter) -> None: self._numeric(f, "p.iso")
    def _f_focal(self, f: Filter) -> None: self._numeric(f, "p.focal_length")
    def _f_fnumber(self, f: Filter) -> None: self._numeric(f, "p.f_number")
    def _f_quality(self, f: Filter) -> None: self._numeric(f, "p.quality")

    def _f_camera(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.camera_id IN (SELECT id FROM cameras WHERE lower(make||' '||model) LIKE ? ESCAPE '\\')", [like(v.lower())]))

    def _f_lens(self, f: Filter) -> None:
        self.any_of(f, lambda v: ("p.lens_id IN (SELECT id FROM lenses WHERE lower(name) LIKE ? ESCAPE '\\')", [like(v.lower())]))

    def _f_color(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "(lower(p.main_color)=? OR (' '||lower(p.colors)||' ') LIKE ?)", [v.lower(), f"% {v.lower()} %"]))

    def _f_name(self, f: Filter) -> None:
        self.any_of(f, lambda v: (
            "p.id IN (SELECT photo_id FROM files WHERE name LIKE ? ESCAPE '\\' OR original_name LIKE ? ESCAPE '\\')",
            [like(v), like(v)]))

    def _f_hash(self, f: Filter) -> None:
        self.any_of(f, lambda v: ("p.id IN (SELECT photo_id FROM files WHERE sha1 LIKE ? ESCAPE '\\')", [prefix_like(v.lower())]))

    def _f_title(self, f: Filter) -> None: self.any_of(f, lambda v: ("p.title LIKE ? ESCAPE '\\'", [like(v)]))
    def _f_caption(self, f: Filter) -> None: self.any_of(f, lambda v: ("p.caption LIKE ? ESCAPE '\\'", [like(v)]))
    def _f_uid(self, f: Filter) -> None: self.any_of(f, lambda v: ("p.uid=?", [v]))

    # --- costruzione --------------------------------------------------------------------
    def build(self, query: Query, default_visibility: bool = True) -> None:
        keys = {f.key for f in query.filters}
        for f in query.filters:
            self.apply(f)
        if default_visibility:
            if "archived" not in keys:
                self.clauses.append("p.deleted_at IS NULL")
            if "private" not in keys:
                self.clauses.append("p.private=0")

    def where(self) -> str:
        return " AND ".join(self.clauses) or "1"


def build_where(conn: sqlite3.Connection, query: Query, default_visibility: bool = True) -> tuple[str, list[Any]]:
    builder = _Builder(conn)
    builder.build(query, default_visibility)
    return builder.where(), builder.params


# --- ordinamento e testo libero ---------------------------------------------------------

ORDER_SQL = {
    "taken": "p.taken_at DESC, p.id DESC", "newest": "p.taken_at DESC, p.id DESC", "oldest": "p.taken_at ASC, p.id ASC",
    "added": "p.created_at DESC, p.id DESC", "name": "p.title COLLATE NOCASE ASC, p.id ASC",
    "quality": "p.quality DESC, p.taken_at DESC", "random": "RANDOM()",
}


def _fts_terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w']+", text.lower()) if t]


def fts_scores(conn: sqlite3.Connection, text: str, candidates: set[int]) -> dict[int, float]:
    """Punteggio FTS5 (0..1) per le foto candidate: prima tutte le parole (AND), altrimenti almeno una (OR)."""
    terms = _fts_terms(text)
    if not terms:
        return {}
    for joiner in (" AND ", " OR "):
        match = joiner.join(fts.escape(t) for t in terms)
        try:
            rows = conn.execute(
                "SELECT rowid, bm25(photos_fts, 6.0, 3.0, 4.0, 2.0, 1.0, 3.0) AS s FROM photos_fts WHERE photos_fts MATCH ?",
                (match,)).fetchall()
        except sqlite3.OperationalError as exc:
            log.debug("query FTS non valida %r: %s", match, exc)
            return {}
        hits = {r["rowid"]: -r["s"] for r in rows if r["rowid"] in candidates}
        if hits:
            top = max(hits.values()) or 1.0
            return {k: max(0.0, v / top) for k, v in hits.items()}
    return {}


def text_embedding(ctx: Context, text: str) -> np.ndarray | None:
    """Embedding SigLIP del testo (con cache LRU); None se il modello non è disponibile."""
    from mosaic.ai.siglip import Siglip

    siglip = Siglip.for_context(ctx)
    if not siglip.available:
        return None
    cache: OrderedDict[str, np.ndarray] = ctx.service("text_cache", OrderedDict)
    key = f"{siglip.model_id}:{text.lower()}"
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    vec = siglip.embed_texts([text])[0]
    cache[key] = vec
    while len(cache) > 128:
        cache.popitem(last=False)
    return vec


def semantic_scores(ctx: Context, text: str, candidates: set[int], limit: int) -> dict[int, float]:
    """Similarità testo→immagine normalizzata (0..1) per le candidate vicine al migliore."""
    from mosaic.ai.vectors import get_index

    index = get_index(ctx)
    if len(index) == 0:
        return {}
    vec = text_embedding(ctx, text)
    if vec is None:
        return {}
    ids, cos = index.search(vec, max(limit, 200), allowed=candidates)
    if ids.size == 0:
        return {}
    best = float(cos[0])
    keep = (cos >= max(SEMANTIC_FLOOR, best - SEMANTIC_BAND))
    ids, cos = ids[keep], cos[keep]
    if ids.size == 0:
        return {}
    low = float(cos.min())
    span = max(float(cos.max()) - low, 1e-6)
    return {int(i): 0.3 + 0.7 * (float(c) - low) / span for i, c in zip(ids, cos, strict=True)}


def rank_text(ctx: Context, text: str, candidates: set[int], limit: int) -> tuple[list[int], bool]:
    sem = semantic_scores(ctx, text, candidates, limit)
    lex = fts_scores(ctx.db.conn(), text, candidates)
    merged = {i: SEMANTIC_WEIGHT * sem.get(i, 0.0) + FTS_WEIGHT * lex.get(i, 0.0) for i in set(sem) | set(lex)}
    ordered = sorted(merged, key=lambda i: (-merged[i], i))
    return ordered, bool(sem)


def photo_summaries(ctx: Context, ids: list[int]) -> list[dict[str, Any]]:
    """Dati essenziali delle foto, nell'ordine richiesto, con l'hash del file principale per le miniature."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = ctx.db.all(
        f"SELECT p.id, p.uid, p.type, p.title, p.caption, p.taken_at_local, p.width, p.height, p.duration, p.favorite, "
        f"p.private, p.quality, p.main_color, p.deleted_at, f.sha1 AS hash FROM photos p "
        f"LEFT JOIN files f ON f.photo_id=p.id AND f.is_primary=1 WHERE p.id IN ({marks})", tuple(ids))
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def search(ctx: Context, q: str = "", count: int = DEFAULT_COUNT, offset: int = 0, order: str = "",
           reverse: bool = False, with_photos: bool = True) -> SearchResult:
    """Cerca nella libreria. Filtri `key:value` in SQL, testo libero ibrido; ordinamento e paginazione."""
    query = parse(q)
    for key in ("count", "offset", "order", "reverse"):
        f = query.get(key)
        if f is None:
            continue
        if key == "count":
            count = int(f.values[0])
        elif key == "offset":
            offset = int(f.values[0])
        elif key == "order":
            order = f.values[0].lower()
        else:
            reverse = to_bool(f.values[0])
    count = max(1, min(MAX_COUNT, count))
    offset = max(0, offset)
    if order and order not in ORDERS:
        raise QueryError(f"ordinamento sconosciuto: {order}")
    where, params = build_where(ctx.db.conn(), query)
    semantic = False
    if query.text:
        candidates = {r[0] for r in ctx.db.conn().execute(f"SELECT p.id FROM photos p WHERE {where}", params)}
        ordered, semantic = rank_text(ctx, query.text, candidates, count + offset)
        if order and order != "relevance":
            ordered = _reorder(ctx, ordered, order)
        total = len(ordered)
        if reverse:
            ordered.reverse()
        ids = ordered[offset:offset + count]
    else:
        order_sql = ORDER_SQL.get(order or "taken", ORDER_SQL["taken"])
        if reverse:
            order_sql = ", ".join(_flip(part) for part in order_sql.split(", "))
        total = ctx.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE {where}", tuple(params))
        ids = [r[0] for r in ctx.db.conn().execute(
            f"SELECT p.id FROM photos p WHERE {where} ORDER BY {order_sql} LIMIT ? OFFSET ?", (*params, count, offset))]
    return SearchResult(ids, total, semantic, photo_summaries(ctx, ids) if with_photos else [])


def _flip(part: str) -> str:
    if part.endswith(" DESC"):
        return part[:-5] + " ASC"
    if part.endswith(" ASC"):
        return part[:-4] + " DESC"
    return part


def _reorder(ctx: Context, ids: list[int], order: str) -> list[int]:
    if not ids:
        return ids
    marks = ",".join("?" * len(ids))
    column = {"taken": "taken_at DESC", "newest": "taken_at DESC", "oldest": "taken_at ASC", "added": "created_at DESC",
              "name": "title COLLATE NOCASE", "quality": "quality DESC", "random": "RANDOM()"}[order]
    return [r[0] for r in ctx.db.conn().execute(f"SELECT id FROM photos WHERE id IN ({marks}) ORDER BY {column}", ids)]


def count_matching(ctx: Context, q: str, default_visibility: bool = True) -> int:
    """Quante foto corrispondono ai soli filtri (senza testo libero): serve ai conteggi degli smart album."""
    where, params = build_where(ctx.db.conn(), parse(q), default_visibility)
    return int(ctx.db.scalar(f"SELECT COUNT(*) FROM photos p WHERE {where}", tuple(params)))
