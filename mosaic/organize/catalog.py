"""Togliere foto e cartelle dal catalogo (D-20), senza mai cancellare in modo definitivo un originale.

Due strade: `remove_photos` (la foto esce dal catalogo; i file restano su disco oppure vanno nel Cestino di Windows) e
`unlink_root` (una cartella della libreria esce dal catalogo con tutto ciò che ne deriva, i file non si toccano).
In entrambi i casi i dati inseriti a mano finiscono prima nel backup YAML (D-17, abbinato all'hash del contenuto), così
tornano da soli se la foto rientra nel catalogo.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.indexer import refresh_primary
from mosaic.index.store import abs_path
from mosaic.organize import albums, backup, moments
from mosaic.organize.people import update_counts
from mosaic.organize.trash import send_to_recycle_bin

log = logging.getLogger(__name__)

UNLINKED_KEY = "unlinked_roots"
CHUNK = 500
TrashFn = Callable[[Iterable[Path]], None]


@dataclass
class RemoveResult:
    removed: int = 0
    files: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)  # (id della foto, motivo)


@dataclass
class UnlinkStats:
    photos: int = 0
    files: int = 0
    folders: int = 0


def _marks(n: int) -> str:
    return ",".join("?" * n)


def drop_photos(conn: sqlite3.Connection, ids: list[int]) -> set[str]:
    """Cancella le foto dal database (il CASCADE porta via file, etichette, embedding, volti e righe degli album)."""
    sha1s: set[str] = set()
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        sha1s |= {r[0] for r in conn.execute(
            f"SELECT DISTINCT sha1 FROM files WHERE photo_id IN ({_marks(len(chunk))})", chunk) if r[0]}
        conn.executemany("DELETE FROM photos_fts WHERE rowid=?", [(i,) for i in chunk])
        conn.execute(f"DELETE FROM photos WHERE id IN ({_marks(len(chunk))})", chunk)
    return sha1s


def drop_cache(ctx: Context, sha1s: Iterable[str]) -> int:
    """Toglie miniature, anteprime ed EXIF in cache degli hash che nessun file del catalogo usa più."""
    removed = 0
    for sha1 in sha1s:
        if len(sha1) < 3 or ctx.db.scalar("SELECT 1 FROM files WHERE sha1=? LIMIT 1", (sha1,)):
            continue
        targets = list((ctx.thumbs_dir / sha1[0] / sha1[1] / sha1[2]).glob(f"{sha1}_*"))
        targets += [ctx.exif_dir / sha1[:2] / f"{sha1}.json", ctx.video_dir / f"{sha1}.mp4"]
        targets += list(ctx.sidecar_dir.glob(f"{sha1}.*"))
        for path in targets:
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def _refresh(ctx: Context) -> None:
    with ctx.db.tx() as conn:
        update_counts(conn)
    albums.refresh_counts(ctx)


def _exclude(conn: sqlite3.Connection, photo_id: int) -> None:
    """Segna come escluso ogni file della foto, così l'indicizzazione non lo riaggiunge."""
    now = utcnow()
    for row in conn.execute(
        "SELECT root, dir, name, sha1, size, mod_time FROM files WHERE photo_id=? AND media_type!='sidecar' AND sha1!=''",
        (photo_id,)).fetchall():
        conn.execute(
            "INSERT OR REPLACE INTO excluded_files(sha1, root, dir, name, size, mod_time, excluded_at) VALUES (?,?,?,?,?,?,?)",
            (row["sha1"], row["root"], row["dir"], row["name"], row["size"], row["mod_time"], now))


def remove_photos(ctx: Context, ids: list[int], mode: str, trash_fn: TrashFn = send_to_recycle_bin) -> RemoveResult:
    """Toglie le foto dal catalogo. `mode`: "catalog" lascia i file su disco, "trash" li manda nel Cestino di Windows."""
    if mode not in ("catalog", "trash"):
        raise ValueError(f"modo di eliminazione non valido: {mode}")
    result = RemoveResult()
    sha1s: set[str] = set()
    for pid in ids:
        files = ctx.db.all("SELECT * FROM files WHERE photo_id=?", (pid,))
        if not files:
            continue
        try:
            backup.write_backup(ctx, pid)  # i dati manuali tornano da soli se la foto rientra nel catalogo
            if mode == "trash":
                trash_fn([abs_path(f) for f in files])
        except OSError as exc:
            result.failed.append((pid, str(exc)))
            gone = [f["id"] for f in files if not abs_path(f).exists()]  # già nel Cestino prima dell'errore
            if gone:
                ctx.db.conn().executemany("UPDATE files SET missing=1 WHERE id=?", [(i,) for i in gone])
            continue
        with ctx.db.tx() as conn:
            if mode == "catalog":
                _exclude(conn, pid)
            sha1s |= drop_photos(conn, [pid])
        result.removed += 1
        result.files += len(files)
    if result.removed:
        drop_cache(ctx, sha1s)
        _refresh(ctx)
    return result


def same_root(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def catalog_roots(ctx: Context) -> list[str]:
    """Radici presenti nel catalogo (file e cartelle), configurate o no."""
    return [r[0] for r in ctx.db.conn().execute("SELECT root FROM files UNION SELECT root FROM folders ORDER BY 1")]


def unlinked_roots(ctx: Context) -> list[str]:
    """Cartelle scollegate e non ancora ricollegate, per proporre «Ricollega»."""
    row = ctx.db.one("SELECT value FROM settings WHERE key=?", (UNLINKED_KEY,))
    try:
        remembered = json.loads(row["value"]) if row else []
    except json.JSONDecodeError:
        remembered = []
    return [r for r in remembered if not any(same_root(r, c) for c in ctx.cfg.library_roots)]


def _remember_unlinked(ctx: Context, root: str) -> None:
    roots = [r for r in unlinked_roots(ctx) if not same_root(r, root)] + [root]
    ctx.db.conn().execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (UNLINKED_KEY, json.dumps(roots, ensure_ascii=False)))


def unlink_root(ctx: Context, root: str) -> UnlinkStats:
    """Toglie dal catalogo una cartella con le sue foto, miniature, embedding, volti, didascalie e album automatici.

    I file su disco non si toccano mai. Titoli, preferiti, parole chiave e nomi dei volti passano prima dal backup YAML.
    Persone e album manuali restano (anche vuoti): ricollegando la cartella si riempiono di nuovo.
    """
    stats = UnlinkStats()
    _remember_unlinked(ctx, root)
    roots = [r for r in catalog_roots(ctx) if same_root(r, root)]
    if not roots:
        return stats
    marks = _marks(len(roots))
    conn = ctx.db.conn()
    photo_ids = [r[0] for r in conn.execute(f"SELECT DISTINCT photo_id FROM files WHERE root IN ({marks})", roots)]
    sha1s = {r[0] for r in conn.execute(f"SELECT DISTINCT sha1 FROM files WHERE root IN ({marks})", roots) if r[0]}
    for pid in photo_ids:
        backup.write_backup(ctx, pid)
    with ctx.db.tx() as tx:
        stats.files = tx.execute(f"DELETE FROM files WHERE root IN ({marks})", roots).rowcount
        tx.execute(f"DELETE FROM duplicates WHERE root IN ({marks})", roots)
        for start in range(0, len(photo_ids), CHUNK):
            chunk = photo_ids[start:start + CHUNK]
            orphans = [r[0] for r in tx.execute(
                f"SELECT id FROM photos WHERE id IN ({_marks(len(chunk))}) AND id NOT IN (SELECT photo_id FROM files)", chunk)]
            drop_photos(tx, orphans)
            stats.photos += len(orphans)
            for pid in set(chunk) - set(orphans):  # foto con file anche in un'altra cartella: restano
                refresh_primary(tx, pid)
        tx.execute(f"UPDATE photos SET folder_id=NULL WHERE folder_id IN (SELECT id FROM folders WHERE root IN ({marks}))", roots)
        stats.folders = tx.execute(f"DELETE FROM folders WHERE root IN ({marks})", roots).rowcount
    drop_cache(ctx, sha1s)
    _refresh(ctx)
    moments.generate(ctx)
    return stats


def excluded_list(ctx: Context) -> list[dict[str, object]]:
    rows = ctx.db.all("SELECT * FROM excluded_files ORDER BY excluded_at DESC, name")
    return [{"sha1": r["sha1"], "name": r["name"], "path": str(abs_path(r)), "size": r["size"],
             "excluded_at": r["excluded_at"], "exists": abs_path(r).exists()} for r in rows]


def restore_excluded(ctx: Context, sha1s: list[str] | None = None) -> int:
    """Rimette nel catalogo (alla prossima indicizzazione) le foto escluse: tutte, o solo quelle indicate."""
    with ctx.db.tx() as conn:
        if sha1s is None:
            return conn.execute("DELETE FROM excluded_files").rowcount
        return sum(conn.execute("DELETE FROM excluded_files WHERE sha1=?", (s,)).rowcount for s in sha1s)


# --- sincronizzazione con il computer (D-24) -------------------------------------------------------

SYNC_MAX_SHARE = 0.5   # se in una cartella manca più della metà dei file insieme, meglio non fidarsi (disco staccato?)
SYNC_MIN_GUARD = 50    # ...ma solo da questo numero di file in su: piccole cartelle svuotate a mano si sincronizzano


@dataclass
class SyncStats:
    files: int = 0
    photos: int = 0
    skipped_roots: list[str] = field(default_factory=list)


def sync_deleted(ctx: Context) -> SyncStats:
    """Modo «Sincronizzata»: i file cancellati dal computer escono anche dal catalogo.

    Cautele: si saltano le cartelle della libreria non raggiungibili (disco staccato, rete), quelle in cui manca
    tutto o all'improvviso più della metà dei file, e ogni file la cui sottocartella non si vede (una cartella
    illeggibile per un attimo non è una cancellazione). Ogni file si ricontrolla sul disco prima di toglierlo.
    I dati manuali passano dal backup YAML.
    """
    stats = SyncStats()
    conn = ctx.db.conn()
    trusted: set[str] = set()
    for r in conn.execute("SELECT root, SUM(missing) AS gone, COUNT(*) AS n FROM files GROUP BY root HAVING gone > 0").fetchall():
        reachable = Path(r["root"]).is_dir()
        suspicious = r["gone"] == r["n"] or (r["gone"] >= SYNC_MIN_GUARD and r["gone"] / r["n"] > SYNC_MAX_SHARE)
        if reachable and not suspicious:
            trusted.add(r["root"])
        else:
            stats.skipped_roots.append(r["root"])
            log.warning("sincronizzazione saltata per %s: %s", r["root"], "non raggiungibile" if not reachable else "troppi file mancanti")
    rows = [r for r in conn.execute("SELECT id, photo_id, root, dir, name, sha1 FROM files WHERE missing=1").fetchall()
            if r["root"] in trusted]
    gone = [r for r in rows if (Path(r["root"]) / r["dir"]).is_dir() and not abs_path(r).exists()]
    if not gone:
        return stats
    file_ids = [r["id"] for r in gone]
    photo_ids = sorted({r["photo_id"] for r in gone})
    sha1s = {r["sha1"] for r in gone if r["sha1"]}
    leaving = set(file_ids)
    for start in range(0, len(photo_ids), CHUNK):  # foto che non avranno più nessun file: backup prima di toglierle
        chunk = photo_ids[start:start + CHUNK]
        alive: set[int] = set()
        for f in conn.execute(f"SELECT id, photo_id FROM files WHERE photo_id IN ({_marks(len(chunk))}) "
                              f"AND media_type!='sidecar'", chunk):
            if f["id"] not in leaving:
                alive.add(f["photo_id"])
        for pid in set(chunk) - alive:
            backup.write_backup(ctx, pid)
    with ctx.db.tx() as tx:
        for start in range(0, len(file_ids), CHUNK):
            chunk = file_ids[start:start + CHUNK]
            stats.files += tx.execute(f"DELETE FROM files WHERE id IN ({_marks(len(chunk))})", chunk).rowcount
        for start in range(0, len(photo_ids), CHUNK):
            chunk = photo_ids[start:start + CHUNK]
            orphans = [r[0] for r in tx.execute(
                f"SELECT id FROM photos WHERE id IN ({_marks(len(chunk))}) AND id NOT IN (SELECT photo_id FROM files)", chunk)]
            drop_photos(tx, orphans)
            stats.photos += len(orphans)
            for pid in set(chunk) - set(orphans):  # restano altri file dello scatto (un RAW, un XMP)
                refresh_primary(tx, pid)
    drop_cache(ctx, sha1s)
    if stats.photos:
        _refresh(ctx)
    return stats
