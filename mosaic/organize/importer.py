"""Import da una cartella esterna dentro la libreria: nome per data (AAAA/MM/AAAAMMGG_HHMMSS_CRC32), mai duplicati."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mosaic.context import Context
from mosaic.index.discover import iter_directories
from mosaic.index.indexer import index_roots
from mosaic.index.related import Group, group_directory, raw_prefix
from mosaic.index.store import abs_path
from mosaic.meta import dates, extract
from mosaic.meta.exiftool import ExifTool, ExifToolError, pillow_exif

log = logging.getLogger(__name__)
BLOCK = 1 << 20


class ImportFailed(RuntimeError):
    """Import impossibile (configurazione o percorsi non validi)."""


@dataclass
class ImportStats:
    imported: int = 0
    skipped: int = 0
    removed_sources: int = 0
    errors: list[str] = field(default_factory=list)
    photo_ids: list[int] = field(default_factory=list)
    dest_root: str = ""


def sha1_and_crc(path: Path) -> tuple[str, str]:
    digest, crc = hashlib.sha1(usedforsecurity=False), 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(BLOCK), b""):
            digest.update(block)
            crc = zlib.crc32(block, crc)
    return digest.hexdigest(), f"{crc & 0xFFFFFFFF:08x}"


def _taken(ctx: Context, main: Path) -> datetime:
    exif = ctx.service("exiftool", ExifTool)
    data: dict = {}
    try:
        data = exif.read(main) if exif.available else pillow_exif(main)
    except ExifToolError:
        data = pillow_exif(main)
    taken = extract.extract(data, main.name, main.stat().st_mtime).taken or dates.from_mtime(main.stat().st_mtime)
    return datetime.fromisoformat(taken.local)


def _indexed_copy(ctx: Context, sha1: str) -> bool:
    for row in ctx.db.all("SELECT * FROM files WHERE sha1=? AND media_type!='sidecar' AND missing=0", (sha1,)):
        if abs_path(row).exists():
            return True
    return False


def _copy_verified(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    shutil.copy2(source, tmp)
    if tmp.stat().st_size != source.stat().st_size:
        tmp.unlink(missing_ok=True)
        raise OSError(f"copia incompleta di {source.name}")
    os.replace(tmp, target)


def _import_group(ctx: Context, group: Group, dest_root: Path, move: bool, stats: ImportStats) -> None:
    main = group.main
    assert main is not None
    sha1, crc = sha1_and_crc(main.abs_path)
    if ctx.db.scalar("SELECT 1 FROM excluded_files WHERE sha1=?", (sha1,)):  # tolta dal catalogo dall'utente (D-20)
        stats.skipped += 1  # la sorgente resta dov'è, anche con move: potrebbe essere l'unica copia
        return
    if _indexed_copy(ctx, sha1):
        stats.skipped += 1
        if move:  # la copia indicizzata esiste davvero: la sorgente è ridondante
            for f in group.files:
                f.abs_path.unlink(missing_ok=True)
                stats.removed_sources += 1
        return
    when = _taken(ctx, main.abs_path)
    folder = dest_root / f"{when:%Y}" / f"{when:%m}"
    base = f"{when:%Y%m%d_%H%M%S}_{crc}"
    main_raw = raw_prefix(main.name)
    plan = []
    for f in group.files:
        raw = raw_prefix(f.name)
        tail = raw[len(main_raw):] if raw.lower().startswith(main_raw.lower()) else ""
        plan.append((f, tail, f.name[len(raw):]))
    candidate, n = base, 0
    while any((folder / f"{candidate}{tail}{ext}").exists() for _, tail, ext in plan):
        n += 1
        candidate = f"{base}.{n:05d}"
    for f, tail, ext in plan:
        _copy_verified(f.abs_path, folder / f"{candidate}{tail}{ext}")
    if move:
        for f, _, _ in plan:
            f.abs_path.unlink(missing_ok=True)
            stats.removed_sources += 1
    stats.imported += 1


def import_files(ctx: Context, source: Path, move: bool = False, dest_root: Path | None = None) -> ImportStats:
    """Importa le foto di `source` nella prima radice della libreria (o in `dest_root`) e le indicizza."""
    source = Path(source).resolve()
    if not source.is_dir():
        raise ImportFailed(f"cartella sorgente non trovata: {source}")
    chosen = dest_root or (ctx.cfg.library_roots[0] if ctx.cfg.library_roots else None)
    if chosen is None:
        raise ImportFailed("nessuna cartella della libreria configurata: aggiungila in config.yaml")
    root = Path(chosen).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if source == root or root in source.parents or source in root.parents:
        raise ImportFailed("la cartella sorgente e la libreria non devono contenersi a vicenda")
    stats = ImportStats(dest_root=str(root))
    for rel_dir, entries in iter_directories(source):
        for group in group_directory(rel_dir, entries, ctx.cfg.stack_sequences):
            try:
                _import_group(ctx, group, root, move, stats)
            except OSError as exc:
                stats.errors.append(f"{group.main.name if group.main else group.key}: {exc}")
                ctx.db.log_error("import", str(source), str(exc))
    if stats.imported:
        stats.photo_ids = index_roots(ctx, [str(root)]).changed_photo_ids
    return stats
