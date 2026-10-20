"""Misure visive di una foto per «Crea selezione» (D-21): nitidezza ed esposizione, calcolate sulla miniatura.

Le misure si salvano in `photo_aesthetics` con lo SHA-1 del file principale: si rifanno solo se il file cambia
o se cambia `VERSION`. Una foto senza miniatura non ha misure e vale «neutra» per chi la usa.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.thumbs.render import thumb_path
from mosaic.thumbs.sizes import get

log = logging.getLogger(__name__)

VERSION = 1
SOURCES = ("fit_720", "tile_500", "tile_224")  # la prima miniatura che esiste
SIDE = 512            # lato massimo su cui si misura: abbastanza per la nitidezza, veloce
GRID = 4              # nitidezza per celle: conta la parte più a fuoco, non lo sfondo sfocato di un ritratto
SHARP_PERCENTILE = 90
WORKERS = 4
BRIGHT = 250         # pixel bruciati
UNDER_LIGHT = 0.35   # sotto questo 90° percentile la foto è buia anche nelle parti chiare
FLAT_CONTRAST = 0.06


@dataclass(frozen=True)
class Measure:
    sharpness: float  # log(1 + varianza del Laplaciano) nella cella più a fuoco
    exposure: float   # 0..1: 1 = esposizione corretta, 0 = quasi tutta bruciata o chiusa


def measure_image(img: Image.Image) -> Measure:
    gray = img.convert("L")
    gray.thumbnail((SIDE, SIDE))
    a = np.asarray(gray, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return Measure(0.0, 0.0)
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    rows = np.array_split(lap, GRID, axis=0)
    cells = [float(c.var()) for r in rows for c in np.array_split(r, GRID, axis=1) if c.size]
    sharp = float(np.log1p(np.percentile(cells, SHARP_PERCENTILE)))
    return Measure(sharp, _exposure(a))


def _exposure(a: np.ndarray) -> float:
    """1 = esposta bene. Penalità: niente di chiaro (sottoesposta), troppe alte luci bruciate, nessun contrasto.

    Lo sfondo nero di una foto notturna con il flash non conta: basta che il soggetto sia chiaro (90° percentile).
    """
    light = float(np.percentile(a, 90)) / 255
    burnt = float((a > BRIGHT).mean())
    contrast = float(a.std()) / 255
    under = np.clip((UNDER_LIGHT - light) / 0.25, 0, 1)
    over = np.clip((burnt - 0.15) / 0.30, 0, 1)
    flat = np.clip((FLAT_CONTRAST - contrast) / 0.04, 0, 1)
    return float(np.clip(1 - (under + over + flat), 0, 1))


def measure_file(path: Path) -> Measure | None:
    try:
        with Image.open(path) as img:
            return measure_image(img)
    except (OSError, ValueError) as exc:
        log.debug("misura non riuscita per %s: %s", path, exc)
        return None


def _thumb(ctx: Context, sha1: str) -> Path | None:
    for name in SOURCES:
        path = thumb_path(ctx.thumbs_dir, sha1, get(name))
        if path.exists():
            return path
    return None


def measures(ctx: Context, photos: Sequence[tuple[int, str | None]]) -> dict[int, Measure]:
    """Misure per (id foto, sha1 del file principale): dalla cache, altrimenti calcolate in parallelo e salvate."""
    wanted = {pid: sha1 for pid, sha1 in photos if sha1}
    if not wanted:
        return {}
    cached: dict[int, Measure] = {}
    ids = list(wanted)
    for start in range(0, len(ids), 900):  # limite dei parametri di SQLite
        chunk = ids[start:start + 900]
        rows = ctx.db.all(
            f"SELECT photo_id, sha1, version, sharpness, exposure FROM photo_aesthetics "
            f"WHERE photo_id IN ({','.join('?' * len(chunk))})", tuple(chunk))
        for r in rows:
            if r["sha1"] == wanted[r["photo_id"]] and r["version"] == VERSION:
                cached[r["photo_id"]] = Measure(r["sharpness"], r["exposure"])
    todo = [(pid, sha1, path) for pid, sha1 in wanted.items() if pid not in cached
            for path in [_thumb(ctx, sha1)] if path is not None]
    if not todo:
        return cached
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="aesthetics") as pool:
        fresh = list(pool.map(lambda item: measure_file(item[2]), todo))
    now = utcnow()
    rows_out = [(pid, sha1, VERSION, m.sharpness, m.exposure, now)
                for (pid, sha1, _), m in zip(todo, fresh, strict=True) if m is not None]
    if rows_out:
        with ctx.db.tx() as conn:
            conn.executemany(
                "INSERT INTO photo_aesthetics(photo_id, sha1, version, sharpness, exposure, measured_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(photo_id) DO UPDATE SET sha1=excluded.sha1, version=excluded.version, "
                "sharpness=excluded.sharpness, exposure=excluded.exposure, measured_at=excluded.measured_at", rows_out)
    return {**cached, **{r[0]: Measure(r[3], r[4]) for r in rows_out}}
