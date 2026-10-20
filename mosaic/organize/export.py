"""Esportazione del catalogo in JSON e CSV (senza embedding), come export_catalog del vecchio Mosaic."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from mosaic.context import Context
from mosaic.index.store import abs_path

COLUMNS = ["uid", "file", "path", "type", "title", "caption", "taken_at", "width", "height", "camera", "lens", "iso",
           "labels", "keywords", "people", "colors", "favorite", "private", "quality"]


def rows(ctx: Context, include_private: bool = False) -> list[dict[str, Any]]:
    conn = ctx.db.conn()
    sql = ("SELECT p.*, c.make AS cmake, c.model AS cmodel, l.name AS lens_name FROM photos p "
           "LEFT JOIN cameras c ON c.id=p.camera_id LEFT JOIN lenses l ON l.id=p.lens_id WHERE p.deleted_at IS NULL")
    if not include_private:
        sql += " AND p.private=0"
    out: list[dict[str, Any]] = []
    for p in conn.execute(sql + " ORDER BY COALESCE(p.taken_at_local,''), p.id").fetchall():
        main = conn.execute("SELECT * FROM files WHERE photo_id=? AND is_primary=1", (p["id"],)).fetchone()
        labels = [r[0] for r in conn.execute(
            "SELECT l.name FROM photos_labels pl JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id=? ORDER BY pl.uncertainty", (p["id"],))]
        keywords = [r[0] for r in conn.execute(
            "SELECT k.word FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id WHERE pk.photo_id=? ORDER BY k.word", (p["id"],))]
        people = [r[0] for r in conn.execute(
            "SELECT DISTINCT s.name FROM markers m JOIN subjects s ON s.id=m.subj_id WHERE m.photo_id=? AND m.invalid=0", (p["id"],))]
        out.append({
            "uid": p["uid"], "file": main["name"] if main else "", "path": str(abs_path(main)) if main else "",
            "type": p["type"], "title": p["title"], "caption": p["caption"], "taken_at": p["taken_at_local"] or "",
            "width": p["width"], "height": p["height"], "camera": f"{p['cmake'] or ''} {p['cmodel'] or ''}".strip(),
            "lens": p["lens_name"] or "", "iso": p["iso"] or "", "labels": labels, "keywords": keywords, "people": people,
            "colors": p["colors"], "favorite": bool(p["favorite"]), "private": bool(p["private"]), "quality": p["quality"],
        })
    return out


def export_catalog(ctx: Context, fmt: str, dest: Path, include_private: bool = False) -> int:
    """Scrive il catalogo; ritorna il numero di foto esportate."""
    data = rows(ctx, include_private)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "csv":
        with dest.open("w", encoding="utf-8-sig", newline="") as fh:  # BOM: Excel apre bene gli accenti
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            for row in data:
                writer.writerow({k: "; ".join(v) if isinstance(v, list) else v for k, v in row.items()})
    else:
        raise ValueError(f"formato non supportato: {fmt}")
    return len(data)
