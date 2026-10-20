"""Normalizza il JSON di exiftool in una struttura `MetaData` indipendente dalla sorgente."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mosaic.meta import dates, sources
from mosaic.meta.dates import Taken

JUNK_DESCRIPTIONS = re.compile(
    r"^(olympus digital camera|sony dsc|dcim|nikon|canon|fujifilm|minolta|kodak|digital camera|\s*)$", re.IGNORECASE
)
# un "titolo" fatto solo di cifre o di un identificativo (numero di archivio, GUID, nome di scatto) non descrive nulla
JUNK_TITLES = re.compile(r"^(\d{4,}|[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|(img|dsc|dscn|pxl|mvimg|vid)[_-]?\d+)$", re.IGNORECASE)
DATE_KEYS = ("SubSecDateTimeOriginal", "DateTimeOriginal", "SubSecCreateDate", "CreateDate", "CreationDate", "MediaCreateDate", "TrackCreateDate")
SWAPPED_ORIENTATIONS = {5, 6, 7, 8}


@dataclass(frozen=True)
class MotionInfo:
    kind: str                # micro | container | binary
    offset_from_end: int = 0  # byte del video contati dalla fine del file
    tag: str = ""             # nome del tag binario di exiftool


@dataclass(frozen=True)
class MetaData:
    taken: Taken | None = None
    taken_src: int = 0
    lat: float | None = None
    lng: float | None = None
    altitude: float | None = None
    make: str = ""
    model: str = ""
    serial: str = ""
    lens: str = ""
    iso: int | None = None
    exposure: str = ""
    f_number: float | None = None
    focal_length: int | None = None
    width: int = 0
    height: int = 0
    orientation: int = 1
    duration: float = 0.0
    title: str = ""
    caption: str = ""
    keywords: tuple[str, ...] = ()
    rating: int = 0
    uuid: str = ""
    codec: str = ""
    fps: float = 0.0
    animated: bool = False
    motion: MotionInfo | None = None
    regions: tuple["Region", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Region:
    name: str
    x: float  # angolo in alto a sinistra, normalizzato 0..1
    y: float
    w: float
    h: float


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return round(number) if number is not None else None


def _text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    return str(value).strip() if value is not None else ""


def _clean_title(title: str) -> str:
    return "" if JUNK_TITLES.match(title) else title


def _keywords(data: dict[str, Any]) -> tuple[str, ...]:
    words: list[str] = []
    for key in ("Subject", "Keywords", "HierarchicalSubject"):
        value = data.get(key)
        if value is None:
            continue
        items = value if isinstance(value, list) else re.split(r"[;,]", str(value))
        for item in items:
            word = str(item).strip().lower()
            if word and word not in words:
                words.append(word)
    return tuple(words)


def _exposure(value: Any) -> str:
    seconds = _num(value)
    if not seconds or seconds <= 0:
        return ""
    return f"1/{round(1 / seconds)}" if seconds < 1 else f"{seconds:g}"


def _taken(data: dict[str, Any], name: str, mtime: float) -> tuple[Taken | None, int]:
    offset = _text(_first(data, "OffsetTimeOriginal", "OffsetTime", "OffsetTimeDigitized")) or None
    for key in DATE_KEYS:
        taken = dates.parse_exif_datetime(_text(data.get(key)), offset)
        if taken:
            return taken, sources.META
    named = dates.parse_filename_datetime(name)
    if named:
        return named, sources.NAME
    modified = dates.parse_exif_datetime(_text(_first(data, "ModifyDate", "FileModifyDate")), offset)
    if modified:
        return modified, sources.MODIFIED
    return (dates.from_mtime(mtime), sources.AUTO) if mtime else (None, 0)


def _motion(data: dict[str, Any]) -> MotionInfo | None:
    if _int(data.get("MicroVideo")) == 1 and (offset := _int(data.get("MicroVideoOffset"))):
        return MotionInfo("micro", offset_from_end=offset)
    mimes, lengths = data.get("DirectoryItemMime"), data.get("DirectoryItemLength")
    if _int(data.get("MotionPhoto")) == 1 and isinstance(mimes, list) and isinstance(lengths, list):
        for mime, length in zip(mimes, lengths, strict=False):
            if str(mime).startswith("video/") and (size := _int(length)):
                return MotionInfo("container", offset_from_end=size)
    for tag in ("MotionPhotoVideo", "EmbeddedVideoFile"):
        if tag in data:
            return MotionInfo("binary", tag=tag)
    return None


def regions(data: dict[str, Any]) -> tuple[Region, ...]:
    """Regioni dei volti MWG-RS (coordinate centrate e normalizzate) → angolo in alto a sinistra."""
    names, xs, ys = data.get("RegionName"), data.get("RegionAreaX"), data.get("RegionAreaY")
    ws, hs, types = data.get("RegionAreaW"), data.get("RegionAreaH"), data.get("RegionType")
    if not all(isinstance(v, list) for v in (names, xs, ys, ws, hs)):
        return ()
    out: list[Region] = []
    for i, label in enumerate(names):
        if isinstance(types, list) and i < len(types) and str(types[i]).lower() not in ("face", ""):
            continue
        try:
            x, y, w, h = float(xs[i]), float(ys[i]), float(ws[i]), float(hs[i])
        except (TypeError, ValueError, IndexError):
            continue
        if w <= 0 or h <= 0:
            continue
        out.append(Region(str(label).strip(), max(0.0, x - w / 2), max(0.0, y - h / 2), min(w, 1.0), min(h, 1.0)))
    return tuple(out)


def extract(data: dict[str, Any], name: str = "", mtime: float = 0.0) -> MetaData:
    """Costruisce i metadati di una foto o un video a partire dal dizionario di exiftool."""
    taken, taken_src = _taken(data, name, mtime)
    lat, lng = _num(data.get("GPSLatitude")), _num(data.get("GPSLongitude"))
    if lat is None or lng is None or (lat == 0 and lng == 0) or abs(lat) > 90 or abs(lng) > 180:
        lat = lng = None
    orientation = _int(data.get("Orientation")) or 1
    width = _int(_first(data, "ImageWidth", "ExifImageWidth", "SourceImageWidth")) or 0
    height = _int(_first(data, "ImageHeight", "ExifImageHeight", "SourceImageHeight")) or 0
    if orientation in SWAPPED_ORIENTATIONS:
        width, height = height, width
    caption = _text(_first(data, "Description", "ImageDescription", "Caption-Abstract", "UserComment"))
    if JUNK_DESCRIPTIONS.match(caption):
        caption = ""
    focal = _num(_first(data, "FocalLength", "FocalLengthIn35mmFormat"))
    frames = _int(data.get("FrameCount")) or 0
    return MetaData(
        taken=taken,
        taken_src=taken_src,
        lat=lat,
        lng=lng,
        altitude=_num(data.get("GPSAltitude")) if lat is not None else None,
        make=_text(data.get("Make")),
        model=_text(data.get("Model")),
        serial=_text(_first(data, "SerialNumber", "InternalSerialNumber", "BodySerialNumber")),
        lens=_text(_first(data, "LensModel", "LensID", "Lens")),
        iso=_int(data.get("ISO")),
        exposure=_exposure(data.get("ExposureTime")),
        f_number=_num(data.get("FNumber")),
        focal_length=round(focal) if focal else None,
        width=width,
        height=height,
        orientation=orientation,
        duration=_num(_first(data, "Duration", "MediaDuration")) or 0.0,
        title=_clean_title(_text(_first(data, "Title", "ObjectName", "DocumentTitle"))),
        caption=caption,
        keywords=_keywords(data),
        rating=max(0, min(5, _int(data.get("Rating")) or 0)),
        uuid=_text(_first(data, "DocumentID", "OriginalDocumentID")),
        codec=_text(_first(data, "CompressorID", "VideoCodec", "CodecID")),
        fps=_num(_first(data, "VideoFrameRate", "FrameRate")) or 0.0,
        animated=frames > 1 or (_int(data.get("AnimationIterations")) is not None and Path(name).suffix.lower() in (".gif", ".webp", ".png")),
        motion=_motion(data),
        regions=regions(data),
    )
