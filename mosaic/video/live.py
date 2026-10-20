"""Live Photo e Motion Photo: riconoscimento della coppia foto+video breve ed estrazione del video incorporato."""

from __future__ import annotations

import logging
from pathlib import Path

from mosaic.context import Context
from mosaic.meta.exiftool import ExifTool
from mosaic.meta.extract import MotionInfo

log = logging.getLogger(__name__)

LIVE_MAX_SECONDS = 3.1          # un video più breve nello stesso stack di un'immagine è una Live Photo
MAX_EMBEDDED_BYTES = 200 << 20  # difesa contro offset assurdi


def is_live_pair(video_duration: float) -> bool:
    return 0 < video_duration <= LIVE_MAX_SECONDS


def _looks_like_mp4(data: bytes) -> bool:
    return len(data) > 12 and data[4:8] == b"ftyp"


def motion_video_path(ctx: Context, sha1: str) -> Path:
    return ctx.sidecar_dir / f"{sha1}.mp4"


def extract_motion_video(ctx: Context, photo: Path, sha1: str, motion: MotionInfo) -> Path | None:
    """Estrae il video incorporato in un JPEG (Google/Samsung). Ritorna il percorso in cache o None."""
    dest = motion_video_path(ctx, sha1)
    if dest.exists():
        return dest
    data = b""
    if motion.kind in ("micro", "container"):
        size = photo.stat().st_size
        length = motion.offset_from_end
        if not 0 < length < min(size, MAX_EMBEDDED_BYTES):
            return None
        with photo.open("rb") as fh:
            fh.seek(size - length)
            data = fh.read(length)
    elif motion.kind == "binary":
        exif = ctx.service("exiftool", ExifTool)
        data = exif.binary(photo, motion.tag)
    if not _looks_like_mp4(data):
        log.info("video incorporato non riconosciuto in %s", photo.name)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest
