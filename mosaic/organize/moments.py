"""Smart album automatici (cartelle, mesi, momenti per etichetta e persona), come internal/photoprism/moments.go."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PureWindowsPath

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.store import new_uid
from mosaic.organize import albums
from mosaic.textutil import slugify

MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre"]


@dataclass
class MomentStats:
    created: int = 0
    updated: int = 0
    removed: int = 0
    threshold: int = 0


def threshold_for(library_size: int) -> int:
    """Soglia minima di foto per un momento: log2(n)+1, come PhotoPrism, con un minimo di 2."""
    return max(2, int(math.log2(max(library_size, 1))) + 1)


def _quote(value: str) -> str:
    return '"' + value.replace('"', " ") + '"'


def _root_albums(conn) -> list[tuple[str, str, str, str]]:  # type: ignore[no-untyped-def]
    """Un album per ogni cartella data alla libreria, con le sue sottocartelle: ci si scrive la giornata."""
    out: list[tuple[str, str, str, str]] = []
    used: set[str] = set()
    for row in conn.execute(
        "SELECT fo.root FROM photos p JOIN folders fo ON fo.id=p.folder_id "
        "WHERE p.deleted_at IS NULL AND p.private=0 GROUP BY fo.root ORDER BY fo.root"):
        name = PureWindowsPath(row["root"]).name or row["root"]
        base = f"radice-{slugify(name) or 'cartella'}"
        slug, n = base, 1
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        out.append(("folder", slug, name, f"root:{_quote(row['root'])}"))
    return out


def _wanted(ctx: Context, threshold: int) -> list[tuple[str, str, str, str]]:
    """(tipo, slug, titolo, filtro) di tutti gli album automatici che meritano di esistere."""
    conn = ctx.db.conn()
    out: list[tuple[str, str, str, str]] = []
    for row in conn.execute(
        "SELECT fo.path, COUNT(*) n FROM photos p JOIN folders fo ON fo.id=p.folder_id "
        "WHERE p.deleted_at IS NULL AND p.private=0 GROUP BY fo.path HAVING n>=1"):
        path = row["path"]
        title = path.rsplit("/", 1)[-1] if path else "Cartella principale"
        out.append(("folder", slugify(path or "radice") or "radice", title, f"folder:{_quote(path)}" if path else ""))
    out = [o for o in out if o[3]]
    out += _root_albums(conn)
    for row in conn.execute(
        "SELECT strftime('%Y-%m', taken_at_local) ym, COUNT(*) n FROM photos WHERE deleted_at IS NULL AND private=0 "
        "AND taken_at_local IS NOT NULL GROUP BY ym HAVING n>=?", (threshold,)):
        year, month = row["ym"].split("-")
        out.append(("month", row["ym"], f"{MONTHS[int(month) - 1].capitalize()} {year}", f"year:{year} month:{int(month)}"))
    for row in conn.execute(
        "SELECT l.slug, l.name, COUNT(DISTINCT pl.photo_id) n FROM labels l JOIN photos_labels pl ON pl.label_id=l.id "
        "JOIN photos p ON p.id=pl.photo_id WHERE p.deleted_at IS NULL AND p.private=0 GROUP BY l.id HAVING n>=?", (threshold,)):
        out.append(("moment", f"etichetta-{row['slug']}", row["name"], f"label:{row['slug']}"))
    for row in conn.execute("SELECT slug, name FROM subjects WHERE hidden=0 AND photo_count>=?", (threshold,)):
        out.append(("moment", f"persona-{row['slug']}", row["name"], f"person:{row['slug']}"))
    return out


def generate(ctx: Context) -> MomentStats:
    """Crea, aggiorna e rimuove gli album automatici. È idempotente e non tocca quelli manuali."""
    conn = ctx.db.conn()
    size = conn.execute("SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL").fetchone()[0]
    stats = MomentStats(threshold=threshold_for(size))
    wanted = _wanted(ctx, stats.threshold)
    keep: set[tuple[str, str]] = set()
    with ctx.db.tx() as tx:
        for kind, slug, title, flt in wanted:
            keep.add((kind, slug))
            row = tx.execute("SELECT id, title, filter FROM albums WHERE type=? AND slug=?", (kind, slug)).fetchone()
            if row is None:
                now = utcnow()
                tx.execute("INSERT INTO albums(uid, type, slug, title, filter, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                           (new_uid("a"), kind, slug, title, flt, now, now))
                stats.created += 1
            elif row["filter"] != flt or row["title"] != title:
                tx.execute("UPDATE albums SET title=?, filter=?, deleted_at=NULL, updated_at=? WHERE id=?",
                           (title, flt, utcnow(), row["id"]))
                stats.updated += 1
        for row in tx.execute("SELECT id, type, slug FROM albums WHERE type IN ('folder','month','moment')").fetchall():
            if (row["type"], row["slug"]) not in keep:
                tx.execute("DELETE FROM albums WHERE id=?", (row["id"],))
                stats.removed += 1
    albums.refresh_counts(ctx)
    return stats
