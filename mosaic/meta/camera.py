"""Normalizzazione di marca e modello della fotocamera; tabelle `cameras` e `lenses`."""

from __future__ import annotations

import sqlite3

MAKES = {
    "nikon corporation": "Nikon", "nikon": "Nikon", "canon": "Canon", "sony": "Sony", "fujifilm": "Fujifilm",
    "fuji photo film co., ltd.": "Fujifilm", "olympus corporation": "Olympus", "olympus imaging corp.": "Olympus",
    "om digital solutions": "OM System", "panasonic": "Panasonic", "apple": "Apple", "samsung": "Samsung",
    "samsung techwin": "Samsung", "google": "Google", "huawei": "Huawei", "xiaomi": "Xiaomi",
    "leica camera ag": "Leica", "leica": "Leica", "pentax": "Pentax", "ricoh imaging company, ltd.": "Ricoh",
    "ricoh": "Ricoh", "dji": "DJI", "gopro": "GoPro", "oneplus": "OnePlus", "motorola": "Motorola",
    "hasselblad": "Hasselblad", "insta360": "Insta360", "kodak": "Kodak", "casio computer co.,ltd.": "Casio",
}


def normalize(make: str | None, model: str | None) -> tuple[str, str]:
    """('NIKON CORPORATION', 'NIKON D850') → ('Nikon', 'D850')."""
    make = (make or "").strip()
    model = (model or "").strip()
    pretty = MAKES.get(make.lower()) or (make.title() if make.isupper() and len(make) > 4 else make)
    first = (make.split() or [""])[0]
    for prefix in {make, pretty, first}:
        if prefix and model.lower().startswith(prefix.lower() + " "):
            model = model[len(prefix) + 1:].strip()
            break
    return pretty, model


def upsert_camera(conn: sqlite3.Connection, make: str | None, model: str | None) -> int | None:
    make, model = normalize(make, model)
    if not make and not model:
        return None
    row = conn.execute("SELECT id FROM cameras WHERE make=? AND model=?", (make, model)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO cameras(make, model) VALUES (?,?)", (make, model)).lastrowid


def upsert_lens(conn: sqlite3.Connection, name: str | None) -> int | None:
    name = (name or "").strip()
    if not name or name.lower() in ("unknown", "n/a", "----"):
        return None
    row = conn.execute("SELECT id FROM lenses WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO lenses(name) VALUES (?)", (name,)).lastrowid
