"""Trascina e rilascia dentro Mosaic (D-25): cartelle, foto e video, archivi zip.

- Una **cartella** si collega dov'è (diventa una cartella della libreria: il percorso originale resta quello) oppure
  si copia nella libreria in `<libreria>/<nome>`, con le stesse sottocartelle e gli stessi nomi (D-31).
- **Foto e video** sciolti si copiano nella libreria (per data, mai duplicati), con i loro XMP accanto.
- Un **.zip** si estrae in una cartella temporanea, in sicurezza, e il contenuto va in `<libreria>/<nome dello zip>`.
- Gli **zip dentro una cartella** rilasciata si estraggono ognuno in una cartella con il loro nome: accanto allo zip se
  la cartella si collega, dentro la copia se si copia (D-31).
- Nel browser i percorsi non sono disponibili: i file arrivano caricati in `data/cache/uploads/<lotto>`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import uuid
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from mosaic.context import Context
from mosaic.index.discover import KNOWN_EXT, SIDECAR_EXT, SKIP_DIR_NAMES, STORAGE_MARKER, FileEntry, iter_directories
from mosaic.index.indexer import index_roots
from mosaic.index.related import Group, group_directory, raw_prefix
from mosaic.organize.importer import ImportStats, _copy_verified, _import_group, _indexed_copy, sha1_and_crc

log = logging.getLogger(__name__)

MAX_WALK = 100_000                 # file contati al massimo per l'anteprima di una cartella
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_BYTES = 40 * 1024 ** 3     # 40 GB estratti al massimo
MAX_ZIP_RATIO = 200                # oltre, sull'insieme dell'archivio, lo zip è sospetto (zip bomb)
RATIO_MIN_BYTES = 64 * 1024 ** 2   # una sola voce enorme e ultracompressa si salta (un BMP piatto da pochi MB no)
SPACE_MARGIN = 256 * 1024 ** 2     # spazio libero da lasciare sul disco dopo l'estrazione
MAX_NESTED_ZIPS = 500              # zip estratti al massimo da una sola cartella rilasciata
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
    folders: int = 0                # cartelle con foto che compariranno in «Cartelle» (sottocartelle e zip compresi)
    zips: int = 0                   # zip dentro la cartella: ognuno si estrae in una cartella con il suo nome
    dest_name: str = ""             # nome della cartella che riceve la copia o l'estrazione


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


def dest_name(path: Path) -> str:
    """Nome della cartella che riceve una cartella o uno zip: il loro nome (senza .zip), mai nascosto né vuoto."""
    name = path.stem if path.suffix.lower() == ".zip" and not path.is_dir() else path.name
    name = name.strip().strip(".").strip()
    return name or (f"Disco {path.drive.rstrip(':')}" if path.drive else "Cartella")


def _with_ancestors(dirs: set[PurePosixPath]) -> set[PurePosixPath]:
    """Le cartelle con foto e quelle che le contengono: ognuna diventa una voce di «Cartelle»."""
    out: set[PurePosixPath] = set()
    for d in dirs:
        out.add(d)
        out.update(d.parents)   # "." è la cartella stessa (o quella con il nome dello zip)
    return out


def _walk(path: Path, exclude: tuple[Path, ...] = ()) -> Iterator[tuple[Path, list[str]]]:
    """os.walk con le stesse cartelle saltate dall'indicizzazione (nascoste, di sistema, con .mosaicstorage)."""
    excluded = {p.resolve() for p in exclude}
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIR_NAMES
                             and not (here / d / STORAGE_MARKER).exists()
                             and not (excluded and (here / d).resolve() in excluded))
        yield here, filenames


def _is_zip(name: str) -> bool:
    return name.lower().endswith(".zip") and not name.startswith(".")


def nested_zips(path: Path, exclude: tuple[Path, ...] = ()) -> list[Path]:
    """Gli zip dentro una cartella, a qualsiasi profondità (al massimo MAX_NESTED_ZIPS)."""
    out: list[Path] = []
    for here, filenames in _walk(path, exclude):
        out += [here / n for n in sorted(filenames) if _is_zip(n)]
        if len(out) >= MAX_NESTED_ZIPS:
            return out[:MAX_NESTED_ZIPS]
    return out


def _count_folder(path: Path, exclude: tuple[Path, ...] = ()) -> tuple[int, int, int, int]:
    """(foto e video, byte, voci di «Cartelle», zip annidati) di una cartella, contenuto degli zip compreso."""
    media, size, seen, zips = 0, 0, 0, 0
    dirs: set[PurePosixPath] = set()
    for here, filenames in _walk(path, exclude):
        rel = PurePosixPath(here.relative_to(path).as_posix())
        for name in filenames:
            seen += 1
            if _is_media(name):
                media += 1
                dirs.add(rel)
                with contextlib.suppress(OSError):
                    size += (here / name).stat().st_size
            elif _is_zip(name) and zips < MAX_NESTED_ZIPS:
                zips += 1
                z_media, z_dirs = _zip_layout(here / name)
                media += z_media
                dirs |= {rel / dest_name(here / name) / d for d in z_dirs}
            if seen >= MAX_WALK:
                return media, size, len(_with_ancestors(dirs)), zips
    return media, size, len(_with_ancestors(dirs)), zips


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
        item.dest_name = dest_name(path)
        if path.is_dir():
            item.kind = "folder"
            item.media, item.size, item.folders, item.zips = _count_folder(path, tuple(roots) if item.contains_library else ())
        elif path.suffix.lower() == ".zip":
            item.kind, item.size = "zip", path.stat().st_size
            item.media, z_dirs = _zip_layout(path)
            item.folders = len(_with_ancestors(z_dirs))
        elif _is_media(path.name):
            item.kind, item.size, item.media = "media", path.stat().st_size, 1
        items.append(item)
    return items


# --- zip --------------------------------------------------------------------------------------------

def _zip_layout(path: Path) -> tuple[int, set[PurePosixPath]]:
    """Foto e video di uno zip e le cartelle interne che li contengono (relative alla cartella con il nome dello zip)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [rel for i in zf.infolist() if not i.is_dir() and _is_media(i.filename)
                     and (rel := _safe_member(i.filename)) is not None]
    except (zipfile.BadZipFile, OSError):
        return 0, set()
    dirs = {n.parent for n in names}
    top = {n.parts[0] for n in names}
    if len(top) == 1 and all(len(n.parts) > 1 for n in names) and top.pop().lower() == dest_name(path).lower():
        dirs = {PurePosixPath(*d.parts[1:]) for d in dirs}   # «Foto.zip» con dentro «Foto/…»: niente Foto/Foto
    return len(names), dirs


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
    """Copia nella libreria file sciolti (con l'XMP accanto, se c'è), per data. L'indicizzazione la fa `run_drop`."""
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
    return stats


def _temp_dir(ctx: Context) -> Path:
    path = ctx.data_dir / "cache" / "imports" / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _zip_source(extracted: Path, name: str) -> Path:
    """Dentro uno zip estratto, la cartella da copiare: si salta l'unica cartella in cima se ha il nome dello zip."""
    children = list(extracted.iterdir())
    if len(children) == 1 and children[0].is_dir() and children[0].name.lower() == name.lower():
        return children[0]
    return extracted


def _copy_group(ctx: Context, group: Group, folder: Path, stats: ImportStats, seen: set[str]) -> None:
    """Copia un gruppo (foto, RAW, XMP, video Live) in `folder` con i nomi originali: mai doppioni, mai sovrascritture."""
    main = group.main
    assert main is not None
    sha1, _ = sha1_and_crc(main.abs_path)
    if sha1 in seen or ctx.db.scalar("SELECT 1 FROM excluded_files WHERE sha1=?", (sha1,)) or _indexed_copy(ctx, sha1):
        stats.skipped += 1
        return
    seen.add(sha1)
    same = folder / main.name
    if same.is_file() and sha1_and_crc(same)[0] == sha1:  # copiata da un rilascio precedente, non ancora indicizzata
        stats.skipped += 1
        return
    plan = [(f, raw_prefix(f.name), f.name[len(raw_prefix(f.name)):]) for f in group.files]
    suffix, n = "", 1
    while any((folder / f"{raw}{suffix}{ext}").exists() for _, raw, ext in plan):
        n += 1
        suffix = f" ({n})"
    for f, raw, ext in plan:
        _copy_verified(f.abs_path, folder / f"{raw}{suffix}{ext}")
    stats.imported += 1


def copy_tree(ctx: Context, source: Path, target: Path, stats: ImportStats, seen: set[str],
              exclude: tuple[Path, ...] = ()) -> None:
    """Copia le foto di `source` in `target` con le stesse sottocartelle; ogni zip dentro va in una cartella col suo nome."""
    for rel_dir, entries in iter_directories(source, exclude):
        folder = target.joinpath(*rel_dir.split("/")) if rel_dir else target
        for group in group_directory(rel_dir, entries, ctx.cfg.stack_sequences):
            if group.main is None:
                continue
            try:
                _copy_group(ctx, group, folder, stats, seen)
            except OSError as exc:
                stats.errors.append(f"{group.main.name}: {exc}")
    for z in nested_zips(source, exclude):
        try:
            copy_zip(ctx, z, target.joinpath(*z.parent.relative_to(source).parts), stats, seen)
        except (DropError, OSError) as exc:
            stats.errors.append(f"{z.name}: {exc}")


def copy_zip(ctx: Context, path: Path, parent: Path, stats: ImportStats, seen: set[str]) -> None:
    """Estrae uno zip in una cartella temporanea e ne copia il contenuto in `parent/<nome dello zip>` (D-31)."""
    name = dest_name(path)
    temp = _temp_dir(ctx)
    try:
        extract_zip(path, temp)
        copy_tree(ctx, _zip_source(temp, name), parent / name, stats, seen)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def copy_folder(ctx: Context, folder: Path, root: Path, stats: ImportStats, seen: set[str]) -> None:
    """Copia una cartella in `<libreria>/<nome>`, con le sue sottocartelle e i suoi zip (D-31)."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise DropError("cartella non trovata")
    exclude = (*_roots(ctx), ctx.data_dir.resolve())  # una cartella che contiene la libreria: la libreria si salta
    _, size, _, _ = _count_folder(folder, exclude)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < size + SPACE_MARGIN:
        raise DropError(f"spazio insufficiente per copiare «{folder.name}» ({size // 1024 ** 2} MB)")
    copy_tree(ctx, folder, root / dest_name(folder), stats, seen, exclude)


def unzip_here(path: Path) -> Path | None:
    """Estrae uno zip accanto a sé, in una cartella con il suo nome (cartelle collegate, D-31).

    Se quella cartella c'è già lo zip si considera estratto e non si tocca niente. Si estrae prima in una cartella
    nascosta (l'indicizzazione la salta) e la si rinomina alla fine: un'estrazione interrotta non lascia mezze cartelle.
    """
    name = dest_name(path)
    final = path.parent / name
    if final.exists():
        return None
    part = path.parent / f".{name}.mosaic-{uuid.uuid4().hex[:8]}"
    try:
        extracted, _ = extract_zip(path, part)
        if not extracted:
            return None
        os.replace(_zip_source(part, name), final)
        return final
    finally:
        shutil.rmtree(part, ignore_errors=True)


def run_drop(ctx: Context, payload: dict, progress: Callable[[int, int, str], None] | None = None) -> DropStats:
    """Esegue il job `drop`: zip da estrarre sul posto, file sciolti, zip, cartelle da copiare, un lotto dal browser."""
    files = [Path(p) for p in payload.get("files", [])]
    zips = [Path(p) for p in payload.get("zips", [])]
    folders = [Path(p) for p in payload.get("copy_folders", [])]
    unzip = [Path(p) for p in payload.get("unzip_here", [])]
    upload = payload.get("upload_dir")
    root = dest_root(ctx) if (files or zips or folders or upload or not unzip) else None
    stats = DropStats()
    copied = ImportStats(dest_root=str(root or ""))
    seen: set[str] = set()   # lo stesso file in due sottocartelle o in due zip: una copia sola
    steps = len(unzip) + (1 if files else 0) + len(zips) + len(folders) + (1 if upload else 0)
    done = 0

    def step(label: str) -> None:
        nonlocal done
        done += 1
        if progress:
            progress(done, steps, label)

    for z in unzip:
        try:
            unzip_here(z)
        except (DropError, OSError) as exc:
            stats.errors.append(f"{z.name}: {exc}")
        step(z.name)
    if root is None:
        return stats
    if files:
        loose = import_loose_files(ctx, [f for f in files if f.is_file()], root)
        copied.imported, copied.skipped, copied.errors = loose.imported, loose.skipped, loose.errors
        step(f"{len(files)} file copiati")
    for z in zips:
        try:
            copy_zip(ctx, z, root, copied, seen)
        except (DropError, OSError) as exc:
            copied.errors.append(f"{z.name}: {exc}")
        step(z.name)
    for folder in folders:
        try:
            copy_folder(ctx, folder, root, copied, seen)
        except (DropError, OSError) as exc:
            copied.errors.append(f"{folder.name}: {exc}")
        step(folder.name)
    if upload:
        _run_upload(ctx, Path(upload), root, copied, seen)
        step("file caricati")
    if copied.imported:
        copied.photo_ids = index_roots(ctx, [str(root)]).changed_photo_ids
    stats.absorb(copied)
    return stats


def _run_upload(ctx: Context, folder: Path, root: Path, stats: ImportStats, seen: set[str]) -> None:
    """Un lotto dal browser, come un rilascio nell'app: cartelle e zip con il loro nome, foto e video sciolti per data."""
    uploads = (ctx.data_dir / "cache" / "uploads").resolve()
    folder = folder.resolve()
    if uploads not in folder.parents:
        raise DropError("cartella di caricamento non valida")
    try:
        loose: list[Path] = []
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            try:
                if entry.is_dir():
                    copy_tree(ctx, entry, root / dest_name(entry), stats, seen)
                elif entry.suffix.lower() == ".zip":
                    copy_zip(ctx, entry, root, stats, seen)
                elif _is_media(entry.name):
                    loose.append(entry)
            except (DropError, OSError) as exc:
                stats.errors.append(f"{entry.name}: {exc}")
        if loose:
            part = import_loose_files(ctx, loose, root)
            stats.imported += part.imported
            stats.skipped += part.skipped
            stats.errors += part.errors
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
