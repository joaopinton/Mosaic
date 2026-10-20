"""Anteprime JPEG per i formati che il browser non mostra (HEIC, RAW, video): cache/sidecar/<sha1>.jpg."""

from __future__ import annotations

import io
import logging
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps

from mosaic.context import Context
from mosaic.index.store import abs_path
from mosaic.meta.exiftool import ExifTool
from mosaic.thumbs.render import open_image
from mosaic.video.poster import extract_poster

log = logging.getLogger(__name__)

PREVIEW_QUALITY = 90
MIN_EMBEDDED_PREVIEW = 1000  # lato lungo minimo per fidarsi dell'anteprima incorporata in un RAW
EMBEDDED_TAGS = ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage")


def sidecar_path(ctx: Context, sha1: str) -> Path:
    return ctx.sidecar_dir / f"{sha1}.jpg"


def _save_jpeg(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    img.convert("RGB").save(tmp, "JPEG", quality=PREVIEW_QUALITY)
    tmp.replace(dest)


def convert_heic(path: Path, dest: Path) -> bool:
    try:
        _save_jpeg(open_image(path), dest)
        return True
    except Exception as exc:  # formato non supportato o file danneggiato
        log.warning("conversione HEIC fallita per %s: %s", path.name, exc)
        return False


def _raw_via_rawpy(path: Path, dest: Path) -> bool:
    try:
        import rawpy
    except ImportError:
        return False
    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = ImageOps.exif_transpose(Image.open(io.BytesIO(thumb.data)))
                elif thumb.format == rawpy.ThumbFormat.BITMAP:
                    img = Image.fromarray(thumb.data)
                else:
                    img = None
                if img is not None and max(img.size) >= MIN_EMBEDDED_PREVIEW:
                    _save_jpeg(img, dest)
                    return True
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                pass
            rgb = raw.postprocess(use_camera_wb=True, half_size=True, no_auto_bright=False)
            _save_jpeg(Image.fromarray(rgb), dest)
            return True
    except Exception as exc:
        log.warning("rawpy non riesce a leggere %s: %s", path.name, exc)
        return False


def _raw_via_exiftool(exif: ExifTool, path: Path, dest: Path) -> bool:
    for tag in EMBEDDED_TAGS:
        data = exif.binary(path, tag)
        if len(data) < 1000:
            continue
        try:
            _save_jpeg(ImageOps.exif_transpose(Image.open(io.BytesIO(data))), dest)
            return True
        except Exception as exc:  # anteprima incorporata danneggiata: si prova il tag successivo
            log.debug("anteprima %s di %s non utilizzabile: %s", tag, path.name, exc)
    return False


def convert_raw(ctx: Context, path: Path, dest: Path) -> bool:
    if _raw_via_rawpy(path, dest):
        return True
    exif = ctx.service("exiftool", ExifTool)
    return exif.available and _raw_via_exiftool(exif, path, dest)


def ensure_source(ctx: Context, row: sqlite3.Row, duration: float = 0.0) -> Path | None:
    """Percorso di un'immagine che Pillow sa aprire per questo file: l'originale o l'anteprima in cache."""
    media = row["media_type"]
    path = abs_path(row)
    if media == "image":
        return path
    dest = sidecar_path(ctx, row["sha1"])
    if dest.exists():
        return dest
    done = False
    if media == "heic":
        done = convert_heic(path, dest)
    elif media == "raw":
        done = convert_raw(ctx, path, dest)
    elif media == "video":
        done = extract_poster(path, dest, duration)
    return dest if done and dest.exists() else None
