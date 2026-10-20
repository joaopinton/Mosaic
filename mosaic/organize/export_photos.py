"""Esporta le foto di una selezione o di un album in una cartella a scelta (D-30).

Si copia il file principale di ogni scatto (con RAW e XMP se richiesti), i nomi restano quelli originali e non si
sovrascrive mai niente. Con «Migliora luce e colori» le foto diventano JPEG corretti da `enhance`; i video si copiano
così come sono. Gli originali nella libreria non si toccano.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from mosaic.context import Context
from mosaic.index.store import abs_path, photo_files, primary_file
from mosaic.organize import enhance
from mosaic.thumbs.convert import ensure_source
from mosaic.thumbs.render import open_image

log = logging.getLogger(__name__)

JPEG_QUALITY = 92
SPACE_MARGIN = 1.05           # margine sullo spazio libero richiesto
PREVIEW_MAX = 4
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_STATS: dict[str, enhance.Stats] = {}  # misure per sha1 (+ volti): non cambiano finché non cambia il file
_STATS_LOCK = threading.Lock()
_STATS_MAX = 4000


class ExportError(ValueError):
    """Destinazione o richiesta non valida."""


@dataclass(frozen=True)
class Options:
    raw: bool = False                        # anche RAW, XMP e la parte video delle Live Photo
    videos: bool = True
    enhance: enhance.Options | None = None   # None: copie identiche


@dataclass
class Result:
    folder: Path
    copied: int = 0
    enhanced: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# --- dove ---------------------------------------------------------------------------------------------

def desktop_dir() -> Path:
    """La Scrivania vera dell'utente (anche se OneDrive la sposta), letta con SHGetKnownFolderPath."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("data", ctypes.c_ubyte * 16)]

            folder_id = GUID()
            folder_id.data[:] = UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}").bytes_le
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value or "")
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, wintypes.LPVOID))
                if path.is_dir():
                    return path
        except (OSError, AttributeError, ValueError):
            log.debug("cartella Scrivania non letta dal sistema", exc_info=True)
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def folder_name(title: str) -> str:
    """Un nome di cartella valido su Windows a partire dal titolo."""
    name = INVALID_NAME.sub(" ", title).strip().rstrip(". ")
    return re.sub(r"\s{2,}", " ", name)[:80] or "Foto Mosaic"


def default_dir(title: str) -> Path:
    return desktop_dir() / folder_name(title)


def check_dest(ctx: Context, dest: str) -> Path:
    """La cartella di destinazione: assoluta, non un file, fuori dai dati di Mosaic."""
    path = Path(dest).expanduser()
    if not dest.strip() or not path.is_absolute():
        raise ExportError("scegli una cartella con il percorso completo, per esempio C:\\Users\\...\\Desktop\\Festa")
    if path.exists() and not path.is_dir():
        raise ExportError("il percorso indicato è un file, non una cartella")
    if _inside(path, Path(ctx.cfg.data_path)):
        raise ExportError("non si può esportare dentro la cartella dei dati di Mosaic")
    return path


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _free_name(folder: Path, name: str, taken: set[str]) -> Path:
    """Il nome originale o, se c'è già, «nome (2).jpg», «nome (3).jpg»… Mai sovrascrivere."""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 1
    while candidate.lower() in taken or (folder / candidate).exists():
        n += 1
        candidate = f"{stem} ({n}){suffix}"
    taken.add(candidate.lower())
    return folder / candidate


# --- cosa ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Item:
    photo_id: int
    main: sqlite3.Row             # riga di `files`
    extra: tuple[sqlite3.Row, ...]


def _items(ctx: Context, uids: list[str], opts: Options) -> tuple[list[_Item], int]:
    """Gli scatti da esportare nell'ordine ricevuto, e quanti sono stati saltati (video esclusi, file mancanti)."""
    if not uids:
        return [], 0
    marks = ",".join("?" * len(uids))
    rows = {r["uid"]: r for r in ctx.db.all(f"SELECT id, uid, type FROM photos WHERE uid IN ({marks})", tuple(uids))}
    items: list[_Item] = []
    skipped = 0
    for uid in dict.fromkeys(uids):
        row = rows.get(uid)
        main = primary_file(ctx.db, row["id"]) if row else None
        if row is None or main is None or (main["media_type"] == "video" and not opts.videos):
            skipped += 1
            continue
        extra = tuple(f for f in photo_files(ctx.db, row["id"]) if f["id"] != main["id"]) if opts.raw else ()
        items.append(_Item(row["id"], main, extra))
    return items, skipped


def _faces(ctx: Context, photo_id: int) -> list[enhance.Face]:
    return [(r["x"], r["y"], r["w"], r["h"]) for r in ctx.db.all(
        "SELECT x, y, w, h FROM markers WHERE photo_id=? AND invalid=0 AND type='face'", (photo_id,))]


def _can_enhance(main: sqlite3.Row) -> bool:
    return main["media_type"] in ("image", "heic", "raw")


def stats_for(ctx: Context, photo_id: int, sha1: str) -> enhance.Stats | None:
    """Misure di una foto sulla miniatura fit_720 (creata se manca), in cache."""
    from mosaic.pipeline import thumb_source

    faces = _faces(ctx, photo_id)
    key = f"{sha1}:{faces}"
    with _STATS_LOCK:
        if key in _STATS:
            return _STATS[key]
    path = thumb_source(ctx, photo_id, "fit_720")
    if path is None:
        return None
    with open_image(path) as img:
        stats = enhance.measure(img, faces)
    with _STATS_LOCK:
        if len(_STATS) >= _STATS_MAX:
            _STATS.clear()
        _STATS[key] = stats
    return stats


def plan_items(ctx: Context, items: list[_Item], opts: enhance.Options,
               cancel: threading.Event | None = None) -> dict[int, enhance.Adjust]:
    """Correzioni per ogni scatto migliorabile, decise sul gruppo intero."""
    measured: list[tuple[int, enhance.Stats]] = []
    for item in items:
        if cancel is not None and cancel.is_set():
            break
        if _can_enhance(item.main):
            stats = stats_for(ctx, item.photo_id, item.main["sha1"])
            if stats is not None:
                measured.append((item.photo_id, stats))
    adjusts = enhance.plan([s for _, s in measured], opts)
    return {pid: adj for (pid, _), adj in zip(measured, adjusts, strict=True)}


def _needed_bytes(items: list[_Item]) -> int:
    return sum(int(f["size"] or 0) for item in items for f in (item.main, *item.extra))


def _check_space(folder: Path, need: int) -> None:
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if need * SPACE_MARGIN > free:
        raise ExportError(f"spazio insufficiente su {probe.anchor or probe}: servono {need / 1e9:.1f} GB, "
                          f"liberi {free / 1e9:.1f} GB")


def _write_enhanced(ctx: Context, main: sqlite3.Row, adjust: enhance.Adjust, target: Path) -> None:
    """La foto corretta come JPEG, con EXIF (orientamento già applicato) e profilo colore dell'originale."""
    source = ensure_source(ctx, main)
    if source is None:
        raise OSError("foto non leggibile")
    with Image.open(source) as original:
        exif = original.getexif()
        icc = original.info.get("icc_profile")
    exif[0x0112] = 1  # i pixel sono già girati
    with open_image(source) as img:
        out = enhance.apply(img, adjust)
    tmp = target.with_name(target.name + ".tmp")
    out.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True, subsampling=0, exif=exif.tobytes(),
             **({"icc_profile": icc} if icc else {}))
    os.replace(tmp, target)


def export(ctx: Context, uids: list[str], dest: Path, opts: Options,
           progress: Callable[[int, int, str], None] | None = None,
           cancel: threading.Event | None = None) -> Result:
    """Copia (o migliora) le foto in `dest`; ritorna quante e gli errori. Si ferma tra una foto e l'altra se annullato."""
    from mosaic.organize.poster import mark_storage

    items, skipped = _items(ctx, uids, opts)
    result = Result(folder=dest, skipped=skipped)
    if not items:
        raise ExportError("nessuna foto da esportare")
    _check_space(dest, _needed_bytes(items))
    dest.mkdir(parents=True, exist_ok=True)
    if any(_inside(dest, Path(root)) for root in ctx.cfg.library_roots):
        mark_storage(dest)  # dentro la libreria: le copie non devono diventare doppioni
    adjusts: dict[int, enhance.Adjust] = {}
    if opts.enhance is not None and opts.enhance.active:
        if progress:
            progress(0, len(items), "Misuro luce e colori…")
        adjusts = plan_items(ctx, items, opts.enhance, cancel)
    taken: set[str] = set()
    for done, item in enumerate(items):
        if cancel is not None and cancel.is_set():
            break
        _export_item(ctx, item, dest, taken, adjusts.get(item.photo_id), result)
        if progress:
            progress(done + 1, len(items), f"{done + 1} di {len(items)} foto")
    return result


def _export_item(ctx: Context, item: _Item, dest: Path, taken: set[str], adjust: enhance.Adjust | None,
                 result: Result) -> None:
    main = item.main
    name = main["name"]
    try:
        if adjust is not None:
            try:
                _write_enhanced(ctx, main, adjust, _free_name(dest, f"{Path(name).stem}.jpg", taken))
                result.enhanced += 1
            except (OSError, ValueError) as exc:  # si ripiega sulla copia identica
                log.warning("miglioramento non riuscito per %s: %s", name, exc)
                result.errors.append(f"{name}: non migliorata ({exc}), copiata com'era")
                shutil.copy2(abs_path(main), _free_name(dest, name, taken))
        else:
            shutil.copy2(abs_path(main), _free_name(dest, name, taken))
        for extra in item.extra:
            shutil.copy2(abs_path(extra), _free_name(dest, extra["name"], taken))
        result.copied += 1
    except OSError as exc:
        result.errors.append(f"{name}: {exc}")


# --- anteprima ----------------------------------------------------------------------------------------

def _data_url(img: Image.Image) -> str:
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def preview(ctx: Context, uids: list[str], opts: enhance.Options, sample: int = PREVIEW_MAX) -> list[dict[str, str]]:
    """Prima e dopo di alcune foto, con le correzioni decise su tutto il gruppo."""
    from mosaic.pipeline import thumb_source

    items, _ = _items(ctx, uids, Options(videos=False))
    adjusts = plan_items(ctx, items, opts)
    out: list[dict[str, str]] = []
    for item in items:
        if len(out) >= sample:
            break
        if item.photo_id not in adjusts:
            continue
        path = thumb_source(ctx, item.photo_id, "fit_720")
        if path is None:
            continue
        with open_image(path) as img:
            before = img.copy()
        before.thumbnail((480, 480))
        uid = ctx.db.one("SELECT uid FROM photos WHERE id=?", (item.photo_id,))["uid"]
        out.append({"uid": uid, "before": _data_url(before), "after": _data_url(enhance.apply(before, adjusts[item.photo_id]))})
    return out
