"""Punteggio di qualità e stato 'da rivedere' (stessa logica di internal/entity/photo_quality.go)."""

from __future__ import annotations

import sqlite3

from mosaic.db import utcnow
from mosaic.meta import sources

REVIEW_BELOW = 3
MIN_PIXELS = 1_000_000


def score(photo: sqlite3.Row | dict, has_thumbs: bool = True) -> int:
    """+3 preferita, +1 data affidabile, +1 posizione, +1 risoluzione, +1 foto 'fotografica'; -1 se rotta."""
    value = 0
    if photo["favorite"]:
        value += 3
    if photo["taken_src"] >= sources.META:
        value += 1
    if photo["lat"] is not None and photo["lng"] is not None:
        value += 1
    if (photo["width"] or 0) * (photo["height"] or 0) >= MIN_PIXELS:
        value += 1
    if photo["camera_id"] is not None or photo["iso"] is not None or photo["focal_length"] is not None:
        value += 1
    if not has_thumbs:
        value -= 1
    return value


def update(conn: sqlite3.Connection, photo_id: int, has_thumbs: bool = True) -> int:
    photo = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if photo is None:
        return 0
    value = score(photo, has_thumbs)
    conn.execute("UPDATE photos SET quality=?, updated_at=? WHERE id=?", (value, utcnow(), photo_id))
    return value


def is_review(quality: int) -> bool:
    return quality < REVIEW_BELOW
