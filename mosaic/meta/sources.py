"""Priorità delle fonti dei metadati: una fonte inferiore non sovrascrive mai una superiore."""

from __future__ import annotations

import sqlite3

from mosaic.db import utcnow

AUTO = 1
ESTIMATE = 2
MODIFIED = 3
NAME = 4
IMAGE = 8
ONNX = 12
META = 16
LLM = 16
XMP = 32
MANUAL = 64
ADMIN = 128

NAMES = {
    AUTO: "auto", ESTIMATE: "stima", MODIFIED: "data di modifica", NAME: "nome file", IMAGE: "immagine",
    ONNX: "modello", META: "metadati", XMP: "xmp", MANUAL: "manuale", ADMIN: "amministratore",
}

# campi con una colonna *_src nella tabella photos
TEXT_FIELDS = {"title": "title_src", "caption": "caption_src"}


def allowed(current_src: int, new_src: int) -> bool:
    """Un valore con fonte `new_src` può sostituire uno con fonte `current_src` solo se non è di priorità inferiore."""
    return new_src >= current_src


def set_text(conn: sqlite3.Connection, photo_id: int, field: str, value: str, src: int) -> bool:
    """Imposta title/caption rispettando la priorità. Ritorna True se il valore è stato scritto."""
    if field not in TEXT_FIELDS:
        raise ValueError(f"campo non gestito: {field}")
    value = (value or "").strip()
    if not value:
        return False
    col = TEXT_FIELDS[field]
    row = conn.execute(f"SELECT {field} AS v, {col} AS s FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None or not allowed(row["s"], src):
        return False
    if row["v"] == value and row["s"] == src:
        return False
    conn.execute(f"UPDATE photos SET {field}=?, {col}=?, updated_at=? WHERE id=?", (value, src, utcnow(), photo_id))
    return True


def set_taken(conn: sqlite3.Connection, photo_id: int, utc: str, local: str, tz: str, src: int) -> bool:
    row = conn.execute("SELECT taken_src FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None or not allowed(row["taken_src"], src):
        return False
    conn.execute(
        "UPDATE photos SET taken_at=?, taken_at_local=?, time_zone=?, taken_src=?, updated_at=? WHERE id=?",
        (utc, local, tz, src, utcnow(), photo_id),
    )
    return True
