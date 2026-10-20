"""Indicizzazione incrementale: gruppi di file, stack, duplicati, spostamenti, file mancanti."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.discover import FileEntry, extension_matches, iter_directories
from mosaic.index.hashing import FileHash, hash_file
from mosaic.index.related import Group, group_directory, stack_key, stack_key_seq
from mosaic.index.store import abs_path, choose_primary, new_uid

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]
MTIME_TOLERANCE = 0.01


@dataclass
class IndexStats:
    scanned: int = 0
    skipped: int = 0
    new_photos: int = 0
    new_files: int = 0
    updated: int = 0
    moved: int = 0
    duplicates: int = 0
    errors: int = 0
    missing: int = 0
    excluded: int = 0
    cancelled: bool = False
    changed_photo_ids: list[int] = field(default_factory=list)


def _same(row: sqlite3.Row, entry: FileEntry) -> bool:
    return abs(row["mod_time"] - entry.mtime) < MTIME_TOLERANCE and row["size"] == entry.size and not row["missing"]


def _folder_id(conn: sqlite3.Connection, root: str, rel_dir: str) -> int:
    row = conn.execute("SELECT id FROM folders WHERE root=? AND path=?", (root, rel_dir)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO folders(root, path) VALUES (?,?)", (root, rel_dir)).lastrowid


def _excluded_by_path(conn: sqlite3.Connection, root: str, entry: FileEntry) -> bool:
    """File che l'utente ha tolto dal catalogo (D-20): stesso percorso, dimensione e data, senza rileggerlo."""
    row = conn.execute("SELECT size, mod_time FROM excluded_files WHERE root=? AND dir=? AND name=?",
                       (root, entry.dir, entry.name)).fetchone()
    return row is not None and row["size"] == entry.size and abs(row["mod_time"] - entry.mtime) < MTIME_TOLERANCE


def _excluded_by_hash(conn: sqlite3.Connection, sha1: str) -> bool:
    return conn.execute("SELECT 1 FROM excluded_files WHERE sha1=?", (sha1,)).fetchone() is not None


def _hash_entries(ctx: Context, entries: list[FileEntry], stats: IndexStats) -> dict[str, FileHash]:
    hashes: dict[str, FileHash] = {}
    for entry in entries:
        try:
            hashes[entry.name] = hash_file(entry.abs_path)
        except OSError as exc:
            stats.errors += 1
            ctx.db.log_error("index", str(entry.abs_path), f"hash non riuscito: {exc}")
    return hashes


def _find_by_hash(conn: sqlite3.Connection, sha1: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM files WHERE sha1=? AND media_type!='sidecar'", (sha1,)
    ).fetchall()


def _target_photo(conn: sqlite3.Connection, group: Group, existing: set[int], stack_sequences: bool) -> int | None:
    if existing:
        return min(existing)
    sample = group.main or group.files[0]
    row = conn.execute(
        "SELECT id FROM photos WHERE stack_key=? AND deleted_at IS NULL ORDER BY id LIMIT 1",
        (stack_key(group.dir, sample.name),),
    ).fetchone()
    if row is None and stack_sequences:
        row = conn.execute(
            "SELECT id FROM photos WHERE stack_key_seq=? AND deleted_at IS NULL ORDER BY id LIMIT 1",
            (stack_key_seq(group.dir, sample.name),),
        ).fetchone()
    return row["id"] if row else None


def _create_photo(conn: sqlite3.Connection, group: Group, folder_id: int) -> int:
    now = utcnow()
    main = group.main
    assert main is not None
    kind = {"video": "video", "raw": "raw"}.get(main.media_type, "image")
    return conn.execute(
        "INSERT INTO photos(uid, type, stack_key, stack_key_seq, folder_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_uid(), kind, stack_key(group.dir, main.name), stack_key_seq(group.dir, main.name), folder_id, now, now),
    ).lastrowid


def refresh_primary(conn: sqlite3.Connection, photo_id: int) -> None:
    rows = conn.execute("SELECT * FROM files WHERE photo_id=? ORDER BY id", (photo_id,)).fetchall()
    primary = choose_primary(rows)
    conn.execute("UPDATE files SET is_primary=0 WHERE photo_id=?", (photo_id,))
    if primary is not None:
        conn.execute("UPDATE files SET is_primary=1 WHERE id=?", (primary,))
        media = next(r["media_type"] for r in rows if r["id"] == primary)
        current = conn.execute("SELECT type FROM photos WHERE id=?", (photo_id,)).fetchone()["type"]
        if current in ("image", "raw", "video"):
            kind = {"video": "video", "raw": "raw"}.get(media, "image")
            conn.execute("UPDATE photos SET type=?, updated_at=? WHERE id=?", (kind, utcnow(), photo_id))


def _process_group(
    ctx: Context, conn_root: str, group: Group, known: dict[str, sqlite3.Row], folder_id: int,
    rescan: bool, stats: IndexStats, seen: set[int],
) -> None:
    entries = group.files
    if group.main is not None and group.main.name not in known and _excluded_by_path(ctx.db.conn(), conn_root, group.main):
        stats.excluded += len(entries)
        return
    changed = rescan or any(e.name not in known or not _same(known[e.name], e) for e in entries)
    for e in entries:
        if e.name in known:
            seen.add(known[e.name]["id"])
    if not changed:
        stats.skipped += len(entries)
        return

    to_hash = [e for e in entries if rescan or e.name not in known or not _same(known[e.name], e)]
    bad = [e for e in to_hash if e.media_type in ("image", "heic") and not extension_matches(e.abs_path)]
    for e in bad:
        stats.errors += 1
        ctx.db.log_error("index", str(e.abs_path), "estensione diversa dal formato reale: file saltato")
    skip_names = {e.name for e in bad}
    if group.main is not None and group.main.name in skip_names:
        return
    to_hash = [e for e in to_hash if e.name not in skip_names]
    hashes = _hash_entries(ctx, to_hash, stats)
    main = group.main
    if main is not None and main.name in {e.name for e in to_hash} and main.name not in hashes:
        return  # il file principale non è leggibile

    content_changed = False
    with ctx.db.tx() as conn:
        existing: set[int] = {known[e.name]["photo_id"] for e in entries if e.name in known}
        new_entries: list[FileEntry] = []
        moved_here = False
        for entry in entries:
            if entry.name in skip_names:
                continue
            info = known.get(entry.name)
            digest = hashes.get(entry.name)
            if info is not None:
                if digest is not None:
                    if digest.sha1 != info["sha1"] and info["media_type"] != "sidecar":
                        content_changed = True
                    conn.execute(
                        "UPDATE files SET sha1=?, sha256=?, size=?, mod_time=?, missing=0, error='' WHERE id=?",
                        (digest.sha1, digest.sha256, digest.size, entry.mtime, info["id"]),
                    )
                    stats.updated += 1
                continue
            if digest is None:
                continue
            if entry.media_type != "sidecar":
                if _excluded_by_hash(conn, digest.sha1):
                    stats.excluded += 1
                    continue
                matches = _find_by_hash(conn, digest.sha1)
                gone = [r for r in matches if not abs_path(r).exists()]
                present = [r for r in matches if abs_path(r).exists()]
                if gone:
                    row = gone[0]
                    conn.execute(
                        "UPDATE files SET root=?, dir=?, name=?, mod_time=?, size=?, missing=0 WHERE id=?",
                        (conn_root, entry.dir, entry.name, entry.mtime, entry.size, row["id"]),
                    )
                    existing.add(row["photo_id"])
                    seen.add(row["id"])
                    stats.moved += 1
                    moved_here = True
                    continue
                if present:
                    conn.execute(
                        "INSERT OR REPLACE INTO duplicates(root, dir, name, sha1, size, mod_time, of_file_id) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (conn_root, entry.dir, entry.name, digest.sha1, entry.size, entry.mtime, present[0]["id"]),
                    )
                    stats.duplicates += 1
                    continue
            new_entries.append(entry)

        photo_id = _target_photo(conn, group, existing, ctx.cfg.stack_sequences)
        created = False
        if photo_id is None:
            if not any(e.media_type != "sidecar" for e in new_entries):
                return
            photo_id = _create_photo(conn, group, folder_id)
            created = True
            stats.new_photos += 1
        for entry in new_entries:
            digest = hashes[entry.name]
            conn.execute(
                "INSERT INTO files(photo_id, root, dir, name, original_name, sha1, sha256, size, mod_time, media_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (photo_id, conn_root, entry.dir, entry.name, entry.name, digest.sha1, digest.sha256,
                 digest.size, entry.mtime, entry.media_type),
            )
            stats.new_files += 1
        if moved_here:
            conn.execute("UPDATE photos SET folder_id=? WHERE id=?", (folder_id, photo_id))
        refresh_primary(conn, photo_id)

        relevant_change = created or bool(new_entries) or content_changed or any(
            e.name in known and e.name in hashes for e in entries
        )
        if relevant_change:
            reset = "meta_at=NULL"
            if content_changed:
                reset += ", analyzed_at=NULL, faces_at=NULL, captioned_at=NULL"
            conn.execute(f"UPDATE photos SET {reset}, updated_at=? WHERE id=?", (utcnow(), photo_id))
            stats.changed_photo_ids.append(photo_id)


def index_roots(
    ctx: Context,
    roots: list[str] | None = None,
    rescan: bool = False,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> IndexStats:
    """Indicizza le radici indicate (o quelle della configurazione). Ritorna le statistiche dell'esecuzione."""
    stats = IndexStats()
    targets = roots if roots is not None else list(ctx.cfg.library_roots)
    for raw_root in targets:
        if roots is None and raw_root not in ctx.cfg.library_roots:
            continue  # la cartella è stata scollegata mentre l'indice girava
        root_path = Path(raw_root)
        if not root_path.is_dir():
            ctx.db.log_error("index", raw_root, "cartella della libreria non trovata")
            stats.errors += 1
            continue
        root = str(root_path.resolve())
        before = {r["id"] for r in ctx.db.all("SELECT id FROM files WHERE root=?", (root,))}
        seen: set[int] = set()
        for rel_dir, entries in iter_directories(root_path, exclude=(ctx.data_dir,)):
            if cancel is not None and cancel.is_set():
                stats.cancelled = True
                return stats
            if roots is None and raw_root not in ctx.cfg.library_roots:
                return stats
            folder_id = _folder_id(ctx.db.conn(), root, rel_dir)
            known = {r["name"]: r for r in ctx.db.all("SELECT * FROM files WHERE root=? AND dir=?", (root, rel_dir))}
            for group in group_directory(rel_dir, entries, ctx.cfg.stack_sequences):
                if cancel is not None and cancel.is_set():  # anche a metà di una cartella con migliaia di file
                    stats.cancelled = True
                    return stats  # prima di segnare i mancanti: i file non ancora visti non sono spariti
                _process_group(ctx, root, group, known, folder_id, rescan, stats, seen)
            stats.scanned += len(entries)
            if progress:
                progress("index", stats.scanned, 0)
        gone = before - seen
        if gone:
            with ctx.db.tx() as conn:
                conn.executemany("UPDATE files SET missing=1 WHERE id=? AND missing=0", [(i,) for i in gone])
            stats.missing += len(gone)
    return stats


def index_paths(ctx: Context, paths: list[Path]) -> IndexStats:
    """Indicizza solo le cartelle indicate, che devono stare dentro una radice nota."""
    roots = [r for r in ctx.cfg.library_roots if any(Path(p).resolve().is_relative_to(Path(r).resolve()) for p in paths)]
    return index_roots(ctx, roots)
