"""Pulizia: file mancanti, righe orfane e cache senza più un file corrispondente."""

from __future__ import annotations

from dataclasses import dataclass

from mosaic.context import Context
from mosaic.index.store import abs_path


@dataclass
class CleanupStats:
    marked_missing: int = 0
    restored: int = 0
    purged_files: int = 0
    purged_photos: int = 0
    cache_removed: int = 0


def verify_files(ctx: Context) -> tuple[int, int]:
    """Confronta il database con il disco: (file segnati mancanti, file ricomparsi)."""
    missing: list[int] = []
    restored: list[int] = []
    for row in ctx.db.all("SELECT id, root, dir, name, missing FROM files"):
        exists = abs_path(row).exists()
        if not exists and not row["missing"]:
            missing.append(row["id"])
        elif exists and row["missing"]:
            restored.append(row["id"])
    with ctx.db.tx() as conn:
        conn.executemany("UPDATE files SET missing=1 WHERE id=?", [(i,) for i in missing])
        conn.executemany("UPDATE files SET missing=0 WHERE id=?", [(i,) for i in restored])
    return len(missing), len(restored)


def purge(ctx: Context) -> tuple[int, int]:
    """Elimina i file mancanti e le foto rimaste senza file. Operazione esplicita, non automatica."""
    with ctx.db.tx() as conn:
        files = conn.execute("DELETE FROM files WHERE missing=1").rowcount
        orphans = [r[0] for r in conn.execute("SELECT id FROM photos WHERE id NOT IN (SELECT DISTINCT photo_id FROM files)")]
        for pid in orphans:
            conn.execute("DELETE FROM photos_fts WHERE rowid=?", (pid,))
            conn.execute("DELETE FROM photos WHERE id=?", (pid,))
    return files, len(orphans)


def purge_cache(ctx: Context) -> int:
    """Rimuove miniature, anteprime ed EXIF in cache che non appartengono più a nessun file noto."""
    known = {r[0] for r in ctx.db.conn().execute("SELECT DISTINCT sha1 FROM files")}
    removed = 0
    for folder in (ctx.thumbs_dir, ctx.sidecar_dir, ctx.video_dir, ctx.exif_dir):
        for path in folder.rglob("*"):
            if path.is_file() and path.name.split("_")[0].split(".")[0] not in known:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def cleanup(ctx: Context, do_purge: bool = False) -> CleanupStats:
    stats = CleanupStats()
    stats.marked_missing, stats.restored = verify_files(ctx)
    if do_purge:
        stats.purged_files, stats.purged_photos = purge(ctx)
        stats.cache_removed = purge_cache(ctx)
    return stats
