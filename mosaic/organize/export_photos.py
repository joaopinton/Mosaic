"""Esporta le foto di una selezione o di un album in una cartella a scelta (D-30).

Si copia il file principale di ogni scatto (con RAW e XMP se richiesti), i nomi restano quelli originali e non si
sovrascrive mai niente. Con «Migliora luce e colori» le foto diventano JPEG corretti da `enhance`; i video si copiano
così come sono. Con «Ritaglia a formato» (D-34) le foto escono JPEG ritagliate sui soggetti (`smart_crop`), e video e
RAW restano fuori. Gli originali nella libreria non si toccano.
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
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image

from mosaic.context import Context
from mosaic.index.store import abs_path, photo_files, primary_file
from mosaic.organize import enhance, smart_crop
from mosaic.thumbs.convert import ensure_source
from mosaic.thumbs.render import open_image

log = logging.getLogger(__name__)

JPEG_QUALITY = 92
FORMATS = {"jpeg": ("JPEG", ".jpg"), "png": ("PNG", ".png"), "webp": ("WEBP", ".webp")}  # «original» = niente conversione
MAX_SIDE_LIMIT = 20000
SPACE_MARGIN = 1.05           # margine sullo spazio libero richiesto
PREVIEW_MAX = 4
CROP_PREVIEW_MAX = 6
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_STATS: dict[str, enhance.Stats] = {}  # misure per sha1 (+ volti): non cambiano finché non cambia il file
_STATS_LOCK = threading.Lock()
_STATS_MAX = 4000
_SALIENCY: dict[str, np.ndarray] = {}  # mappe del soggetto per sha1 (piccole: 64 pixel di lato)
_SALIENCY_MAX = 1000


class ExportError(ValueError):
    """Destinazione o richiesta non valida."""


@dataclass(frozen=True)
class Options:
    raw: bool = False                        # anche RAW, XMP e la parte video delle Live Photo
    videos: bool = True
    enhance: enhance.Options | None = None   # None: copie identiche
    crop: smart_crop.Format | None = None    # None: nessun ritaglio (D-34)
    fmt: str = "original"                    # «original» o jpeg/png/webp: le foto si riscrivono in quel formato (D-36)
    quality: int = JPEG_QUALITY              # 40-100, per JPEG e WebP
    max_side: int | None = None              # lato lungo massimo in pixel (le foto più piccole non si ingrandiscono)
    numbered: bool = False                   # nomi «0001_nome» nell'ordine scelto

    @property
    def rewrites(self) -> bool:
        """Le foto vanno riscritte (conversione o riduzione), non copiate così come sono."""
        return self.fmt in FORMATS or self.max_side is not None


@dataclass
class Result:
    folder: Path
    copied: int = 0
    enhanced: int = 0
    cropped: int = 0
    skipped: int = 0
    unimproved: int = 0
    not_found: int = 0   # l'originale non c'è più dove il catalogo dice (spostato, rinominato, disco scollegato)
    converted: int = 0
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
        unwanted = main is not None and ((main["media_type"] == "video" and not opts.videos)
                                         or (opts.crop is not None and not _can_enhance(main)))  # si ritagliano solo foto
        if row is None or main is None or unwanted:
            skipped += 1
            continue
        extra = tuple(f for f in photo_files(ctx.db, row["id"]) if f["id"] != main["id"]
                      and (opts.raw if f["media_type"] != "video" else opts.videos))
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


def saliency_for(ctx: Context, photo_id: int, sha1: str) -> np.ndarray | None:
    """Mappa del soggetto principale sulla miniatura fit_720 (creata se manca), in cache: la stessa per anteprima e file."""
    from mosaic.pipeline import thumb_source

    with _STATS_LOCK:
        if sha1 in _SALIENCY:
            return _SALIENCY[sha1]
    path = thumb_source(ctx, photo_id, "fit_720")
    if path is None:
        return None
    with open_image(path) as img:
        sal = smart_crop.saliency(img)
    with _STATS_LOCK:
        if len(_SALIENCY) >= _SALIENCY_MAX:
            _SALIENCY.clear()
        _SALIENCY[sha1] = sal
    return sal


def crop_for(ctx: Context, photo_id: int, sha1: str, width: int, height: int, fmt: smart_crop.Format) -> smart_crop.Box:
    """Il ritaglio di una foto `width`×`height`: sui volti se ci sono, altrimenti sul soggetto principale."""
    faces = _faces(ctx, photo_id)
    sal = None if faces else saliency_for(ctx, photo_id, sha1)
    return smart_crop.crop_box(width, height, fmt, faces, sal)


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


def _out_format(opts: Options) -> tuple[str, str]:
    """(formato Pillow, estensione) delle foto riscritte: quello scelto, altrimenti JPEG."""
    return FORMATS.get(opts.fmt, FORMATS["jpeg"])


def _write_photo(ctx: Context, item: _Item, adjust: enhance.Adjust | None, opts: Options, target: Path) -> None:
    """La foto ritagliata, corretta e/o convertita, con EXIF (orientamento già applicato) e profilo colore dell'originale."""
    main = item.main
    source = ensure_source(ctx, main)
    if source is None:
        raise OSError("foto non leggibile")
    with Image.open(source) as original:
        exif = original.getexif()
        icc = original.info.get("icc_profile")
    exif[0x0112] = 1  # i pixel sono già girati
    pil_format, _ = _out_format(opts)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open_image(source) as img:
            out = img
            if opts.crop is not None:
                out = smart_crop.apply(img, crop_for(ctx, item.photo_id, main["sha1"], img.width, img.height, opts.crop), opts.crop)
            if adjust is not None:
                out = enhance.apply(out, adjust)
            if opts.max_side is not None and max(out.size) > opts.max_side:
                out = out.copy()
                out.thumbnail((opts.max_side, opts.max_side), Image.Resampling.LANCZOS)
            if out.mode not in ("RGB", "RGBA", "L") or (pil_format == "JPEG" and out.mode != "RGB" and out.mode != "L"):
                out = out.convert("RGB")
            exif_ifd = exif.get_ifd(0x8769)
            if exif_ifd:  # le dimensioni scritte nell'EXIF seguono il ritaglio
                exif_ifd[0xA002], exif_ifd[0xA003] = out.size
            extra: dict[str, object] = {"exif": exif.tobytes()}
            if icc:
                extra["icc_profile"] = icc
            if pil_format == "JPEG":
                extra.update(quality=opts.quality, optimize=True, subsampling=0)
            elif pil_format == "WEBP":
                extra.update(quality=opts.quality, method=4)
            else:
                extra.update(optimize=True)
            out.save(tmp, pil_format, **extra)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)  # un errore a metà non lascia file «.tmp» nella cartella dell'utente


def export(ctx: Context, uids: list[str], dest: Path, opts: Options,
           progress: Callable[[int, int, str], None] | None = None,
           cancel: threading.Event | None = None) -> Result:
    """Copia (o migliora) le foto in `dest`; ritorna quante e gli errori. Si ferma tra una foto e l'altra se annullato."""
    from mosaic.organize.poster import mark_storage

    if opts.crop is not None:  # si ritagliano solo le foto: niente video, RAW o XMP accanto alle copie
        opts = replace(opts, raw=False, videos=False)
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
    width = max(4, len(str(len(items))))
    for done, item in enumerate(items):
        if cancel is not None and cancel.is_set():
            break
        prefix = f"{done + 1:0{width}d}_" if opts.numbered else ""
        _export_item(ctx, item, dest, taken, adjusts.get(item.photo_id), opts, result, prefix)
        if progress:
            progress(done + 1, len(items), f"{done + 1} di {len(items)} foto")
    if result.not_found:
        _flag_missing(ctx, items)
    if result.copied == 0 and not (cancel is not None and cancel.is_set()):
        for error in result.errors[:20]:
            ctx.db.log_error("export", "", error)
        raise ExportError(_nothing_exported(result, len(items)))
    return result


def _nothing_exported(result: Result, total: int) -> str:
    """Perché non è stata esportata nessuna foto, in parole che dicono cosa fare."""
    if result.not_found:
        return (f"nessuna foto esportata: {result.not_found} su {total} non si trovano più dove Mosaic le ha registrate "
                "(cartella o foto spostata o rinominata, disco scollegato?). In Libreria usa «Indica la nuova posizione…» "
                "se hai spostato la cartella, oppure «Cerca nuove foto»")
    return f"nessuna foto esportata ({result.errors[0]})" if result.errors else "nessuna foto esportata"


def _flag_missing(ctx: Context, items: list[_Item]) -> None:
    """I file che non esistono più su disco si segnano mancanti, come farebbe una verifica della libreria."""
    gone = [f["id"] for item in items for f in (item.main, *item.extra) if not abs_path(f).exists()]
    if gone:
        with ctx.db.tx() as conn:
            conn.executemany("UPDATE files SET missing=1 WHERE id=?", [(i,) for i in gone])


def _export_item(ctx: Context, item: _Item, dest: Path, taken: set[str], adjust: enhance.Adjust | None,
                 opts: Options, result: Result, prefix: str = "") -> None:
    main = item.main
    name = main["name"]
    wants_look = opts.enhance is not None and opts.enhance.active and _can_enhance(main)
    ext = _out_format(opts)[1]
    if not abs_path(main).exists():  # spostata o rinominata dopo l'indicizzazione
        result.not_found += 1
        result.errors.append(f"{name}: file non trovato ({abs_path(main)})")
        return
    try:
        if opts.crop is not None:  # niente ripiego sulla copia identica: si vogliono solo foto nel formato scelto
            try:
                _write_photo(ctx, item, adjust, opts, _free_name(dest, f"{prefix}{Path(name).stem}{ext}", taken))
            except (OSError, ValueError) as exc:
                log.warning("ritaglio non riuscito per %s: %s", name, exc)
                result.errors.append(f"{name}: non ritagliata ({exc})")
                return
            result.cropped += 1
            result.enhanced += adjust is not None
        elif (adjust is not None or opts.rewrites) and _can_enhance(main):
            try:
                _write_photo(ctx, item, adjust, opts, _free_name(dest, f"{prefix}{Path(name).stem}{ext}", taken))
                result.enhanced += adjust is not None
                result.converted += opts.rewrites
                result.unimproved += wants_look and adjust is None  # misure non disponibili: nessuna correzione applicata
            except (OSError, ValueError) as exc:  # si ripiega sulla copia identica
                log.warning("esportazione non riuscita per %s: %s", name, exc)
                result.errors.append(f"{name}: non convertita ({exc}), copiata com'era")
                shutil.copy2(abs_path(main), _free_name(dest, prefix + name, taken))
        else:
            result.unimproved += wants_look  # niente correzione calcolata: copiata com'era
            shutil.copy2(abs_path(main), _free_name(dest, prefix + name, taken))
        for extra in item.extra:
            shutil.copy2(abs_path(extra), _free_name(dest, prefix + extra["name"], taken))
        result.copied += 1
    except FileNotFoundError as exc:
        result.not_found += 1
        result.errors.append(f"{name}: file non trovato ({exc})")
    except OSError as exc:
        result.errors.append(f"{name}: {exc}")


def make_zip(folder: Path, target: Path) -> Path:
    """Impacchetta i file di `folder` in `target` (zip senza compressione per le foto già compresse)."""
    import zipfile

    tmp = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(folder).as_posix())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def zip_dir(ctx: Context) -> Path:
    """Dove stanno gli zip pronti da scaricare (cache, si ripuliscono da soli)."""
    path = ctx.data_dir / "cache" / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prune_zips(ctx: Context, keep: int = 3) -> None:
    """Tiene solo gli ultimi `keep` zip: sono copie temporanee e pesano come le foto."""
    files = sorted(zip_dir(ctx).glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


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


def crop_preview(ctx: Context, uids: list[str], fmt: smart_crop.Format,
                 sample: int = CROP_PREVIEW_MAX) -> list[dict[str, object]]:
    """Alcune foto con il riquadro del ritaglio in frazioni (x0, y0, x1, y1), calcolato come per il file."""
    from mosaic.pipeline import thumb_source

    items, _ = _items(ctx, uids, Options(videos=False, crop=fmt))
    out: list[dict[str, object]] = []
    for item in items:
        if len(out) >= sample:
            break
        path = thumb_source(ctx, item.photo_id, "fit_720")
        if path is None:
            continue
        with open_image(path) as img:
            x0, y0, x1, y1 = crop_for(ctx, item.photo_id, item.main["sha1"], img.width, img.height, fmt)
            w, h = img.size
            small = img.copy()
        small.thumbnail((480, 480))
        uid = ctx.db.one("SELECT uid FROM photos WHERE id=?", (item.photo_id,))["uid"]
        out.append({"uid": uid, "image": _data_url(small), "box": [x0 / w, y0 / h, x1 / w, y1 / h]})
    return out
