"""Trascina e rilascia dentro Mosaic (D-25): cartelle, foto e video, archivi zip.

- Una **cartella** si collega dov'è (diventa una cartella della libreria: il percorso originale resta quello) oppure
  si copia nella libreria come un normale import.
- **Foto e video** sciolti si copiano nella libreria (per data, mai duplicati), con i loro XMP accanto.
- Un **.zip** si estrae in una cartella temporanea, in sicurezza, e il contenuto si importa come una cartella.
- Nel browser i percorsi non sono disponibili: i file arrivano caricati in `data/cache/uploads/<lotto>`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from mosaic.context import Context
from mosaic.index.discover import KNOWN_EXT, SIDECAR_EXT, FileEntry, classify
from mosaic.index.indexer import index_roots
from mosaic.index.related import group_directory
from mosaic.organize.importer import ImportFailed, ImportStats, _import_group, import_files

log = logging.getLogger(__name__)

MAX_WALK = 100_000                 # file contati al massimo per l'anteprima di una cartella
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_BYTES = 40 * 1024 ** 3     # 40 GB estratti al massimo
MAX_ZIP_RATIO = 200                # oltre, sull'insieme dell'archivio, lo zip è sospetto (zip bomb)
RATIO_MIN_BYTES = 64 * 1024 ** 2   # una sola voce enorme e ultracompressa si salta (un BMP piatto da pochi MB no)
SPACE_MARGIN = 256 * 1024 ** 2     # spazio libero da lasciare sul disco dopo l'estrazione
BATCH_RE = re.compile(r"^[a-z0-9]{8,32}$")
KINDS = ("folder", "media", "zip", "unsupported", "missing")


class DropError(ValueError):
    """Elemento rilasciato non utilizzabile (con un messaggio per l'utente)."""


@dataclass
class DropItem:
    path: str
    name: str
    kind: str
    size: int = 0
    media: int = 0
    in_library: bool = False        # sta già dentro una cartella della libreria: basta indicizzare
    contains_library: bool = False  # contiene una cartella della libreria: non si può collegare


@dataclass
class DropStats:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    photo_ids: list[int] = field(default_factory=list)

    def absorb(self, other: ImportStats) -> None:
        self.imported += other.imported
        self.skipped += other.skipped
        self.errors += other.errors
        self.photo_ids += other.photo_ids


def _is_media(name: str) -> bool:
    return Path(name).suffix.lower() in KNOWN_EXT and Path(name).suffix.lower() not in SIDECAR_EXT


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _roots(ctx: Context) -> list[Path]:
    return [Path(r).resolve() for r in ctx.cfg.library_roots]


def _count_folder(path: Path) -> tuple[int, int]:
    media, size, seen = 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            seen += 1
            if _is_media(name):
                media += 1
                try:
                    size += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
            if seen >= MAX_WALK:
                return media, size
    return media, size


def inspect(ctx: Context, paths: list[str]) -> list[DropItem]:
    """Cosa è stato rilasciato, per il dialogo di conferma."""
    roots = _roots(ctx)
    items: list[DropItem] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute() or not path.exists():
            items.append(DropItem(raw, path.name or raw, "missing"))
            continue
        path = path.resolve()
        item = DropItem(str(path), path.name or str(path), "unsupported",
                        in_library=any(_inside(path, r) for r in roots),
                        contains_library=any(r != path and path in r.parents for r in roots))
        if path.is_dir():
            item.kind = "folder"
            item.media, item.size = _count_folder(path)
        elif path.suffix.lower() == ".zip":
            item.kind, item.size = "zip", path.stat().st_size
            item.media = _zip_media_count(path)
        elif _is_media(path.name):
            item.kind, item.size, item.media = "media", path.stat().st_size, 1
        items.append(item)
    return items


# --- zip --------------------------------------------------------------------------------------------

def _zip_media_count(path: Path) -> int:
    try:
        with zipfile.ZipFile(path) as zf:
            return sum(1 for i in zf.infolist() if not i.is_dir() and _is_media(i.filename))
    except (zipfile.BadZipFile, OSError):
        return 0


def _safe_member(name: str) -> PurePosixPath | None:
    """Percorso relativo sicuro dentro lo zip, oppure None (assoluto, con .., con lettera di unità, nascosto)."""
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or name.startswith(("/", "\\")) or any(p in ("..", "") or ":" in p for p in parts):
        return None
    if any(p.startswith(".") or p.lower() == "__macosx" for p in parts):
        return None
    return PurePosixPath(*parts)


def extract_zip(path: Path, target: Path) -> tuple[int, int]:
    """Estrae solo foto, video e XMP in `target`. Ritorna (estratti, saltati). Rifiuta zip bomb e disco pieno."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DropError(f"«{path.name}» non è uno zip leggibile") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_ENTRIES:
            raise DropError(f"«{path.name}» contiene troppi file ({len(infos)})")
        wanted = [(i, _safe_member(i.filename)) for i in infos if Path(i.filename).suffix.lower() in KNOWN_EXT]
        wanted = [(i, rel) for i, rel in wanted if rel is not None]
        total = sum(i.file_size for i, _ in wanted)
        if total > MAX_ZIP_BYTES:
            raise DropError(f"«{path.name}» è troppo grande da estrarre ({total // 1024 ** 3} GB)")
        packed = sum(i.compress_size for i, _ in wanted)
        if packed and total / packed > MAX_ZIP_RATIO:
            raise DropError(f"«{path.name}» ha un rapporto di compressione sospetto: non lo estraggo")
        wanted = [(i, rel) for i, rel in wanted if not (i.file_size > RATIO_MIN_BYTES and i.compress_size
                                                        and i.file_size / i.compress_size > MAX_ZIP_RATIO)]
        total = sum(i.file_size for i, _ in wanted)
        target.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(target).free < total + SPACE_MARGIN:
            raise DropError(f"spazio insufficiente per estrarre «{path.name}» ({total // 1024 ** 2} MB)")
        extracted = 0
        for info, rel in wanted:
            dest = target.joinpath(*rel.parts)
            if not _inside(dest.resolve(), target.resolve()):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            extracted += 1
        return extracted, len(infos) - extracted


# --- import ----------------------------------------------------------------------------------------

def dest_root(ctx: Context) -> Path:
    """Dove finiscono le copie: la prima cartella della libreria (come «Importa»)."""
    if not ctx.cfg.library_roots:
        raise DropError("collega prima una cartella alla libreria: le copie vanno lì")
    return Path(ctx.cfg.library_roots[0]).resolve()


def import_loose_files(ctx: Context, files: list[Path], root: Path) -> ImportStats:
    """Copia nella libreria file sciolti (con l'XMP accanto, se c'è) e li indicizza."""
    stats = ImportStats(dest_root=str(root))
    by_dir: dict[Path, set[str]] = {}
    for f in files:
        by_dir.setdefault(f.parent, set()).add(f.name)
    for directory, names in by_dir.items():
        stems = {Path(n).stem.lower() for n in names}
        entries = []
        for p in sorted(directory.iterdir(), key=lambda q: q.name.lower()):
            sidecar = p.suffix.lower() in SIDECAR_EXT and (p.stem.lower() in stems or Path(p.stem).stem.lower() in stems)
            if p.is_file() and (p.name in names or sidecar):
                st = p.stat()
                entries.append(FileEntry(p.name, "", p, st.st_size, st.st_mtime))
        for group in group_directory("", entries, ctx.cfg.stack_sequences):
            try:
                _import_group(ctx, group, root, False, stats)
            except OSError as exc:
                stats.errors.append(f"{group.main.name if group.main else group.key}: {exc}")
    if stats.imported:
        stats.photo_ids = index_roots(ctx, [str(root)]).changed_photo_ids
    return stats


def _temp_dir(ctx: Context) -> Path:
    path = ctx.data_dir / "cache" / "imports" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _import_zip(ctx: Context, path: Path, root: Path, stats: DropStats) -> None:
    temp = _temp_dir(ctx)
    try:
        extract_zip(path, temp)
        stats.absorb(import_files(ctx, temp, dest_root=root))
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_drop(ctx: Context, payload: dict, progress: Callable[[int, int, str], None] | None = None) -> DropStats:
    """Esegue il job `drop`: file sciolti, zip, cartelle da copiare, oppure un lotto caricato dal browser."""
    root = dest_root(ctx)
    stats = DropStats()
    files = [Path(p) for p in payload.get("files", [])]
    zips = [Path(p) for p in payload.get("zips", [])]
    folders = [Path(p) for p in payload.get("copy_folders", [])]
    upload = payload.get("upload_dir")
    steps = (1 if files else 0) + len(zips) + len(folders) + (1 if upload else 0)
    done = 0

    def step(label: str) -> None:
        nonlocal done
        done += 1
        if progress:
            progress(done, steps, label)

    if files:
        stats.absorb(import_loose_files(ctx, [f for f in files if f.is_file()], root))
        step(f"{len(files)} file copiati")
    for z in zips:
        try:
            _import_zip(ctx, z, root, stats)
        except (DropError, ImportFailed, OSError) as exc:
            stats.errors.append(f"{z.name}: {exc}")
        step(z.name)
    for folder in folders:
        try:
            stats.absorb(import_files(ctx, folder, dest_root=root))
        except (ImportFailed, OSError) as exc:
            stats.errors.append(f"{folder.name}: {exc}")
        step(folder.name)
    if upload:
        _run_upload(ctx, Path(upload), root, stats)
        step("file caricati")
    return stats


def _run_upload(ctx: Context, folder: Path, root: Path, stats: DropStats) -> None:
    uploads = (ctx.data_dir / "cache" / "uploads").resolve()
    folder = folder.resolve()
    if uploads not in folder.parents:
        raise DropError("cartella di caricamento non valida")
    try:
        for z in sorted(p for p in folder.rglob("*.zip") if p.is_file()):
            try:
                extract_zip(z, z.with_name(z.stem + "_zip"))
                z.unlink(missing_ok=True)
            except (DropError, OSError) as exc:
                stats.errors.append(f"{z.name}: {exc}")
        stats.absorb(import_files(ctx, folder, dest_root=root))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- caricamento dal browser -------------------------------------------------------------------------

def upload_target(ctx: Context, batch: str, rel: str) -> Path:
    """Percorso sicuro di un file caricato: solo dentro `data/cache/uploads/<lotto>`, solo foto, video, XMP e zip."""
    if not BATCH_RE.match(batch):
        raise DropError("lotto di caricamento non valido")
    safe = _safe_member(rel)
    if safe is None or not (Path(safe.name).suffix.lower() in KNOWN_EXT or safe.suffix.lower() == ".zip"):
        raise DropError(f"file non supportato: {rel}")
    base = (ctx.data_dir / "cache" / "uploads" / batch).resolve()
    target = base.joinpath(*safe.parts).resolve()
    if not _inside(target, base):
        raise DropError("percorso non valido")
    return target


def upload_dir(ctx: Context, batch: str) -> Path:
    if not BATCH_RE.match(batch):
        raise DropError("lotto di caricamento non valido")
    return ctx.data_dir / "cache" / "uploads" / batch
