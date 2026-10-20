"""Elaborazione di una foto dopo l'indicizzazione: metadati, anteprime, miniature, colori, tipo e qualità."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.indexer import refresh_primary
from mosaic.index.stack import stack_photo
from mosaic.index.store import abs_path, new_uid, photo_files, primary_file
from mosaic.meta import exiftool as et
from mosaic.meta import extract, sources, takeout
from mosaic.meta.camera import upsert_camera, upsert_lens
from mosaic.meta.exiftool import ExifTool, ExifToolError
from mosaic.meta.extract import MetaData
from mosaic.organize import quality, tags
from mosaic.organize.people import ensure_subject
from mosaic.search import fts
from mosaic.thumbs import colors
from mosaic.thumbs.convert import ensure_source
from mosaic.thumbs.render import generate_set, thumb_path
from mosaic.thumbs.sizes import get as get_size
from mosaic.video import live, probe, transcode

log = logging.getLogger(__name__)

ANIMATED_EXT = {".gif", ".webp", ".png"}


def read_exif(ctx: Context, row: sqlite3.Row) -> dict[str, Any]:
    """Metadati grezzi del file: cache per hash → exiftool → fallback Pillow."""
    cached = et.read_cached(ctx.exif_dir, row["sha1"])
    if cached is not None:
        return cached
    path = abs_path(row)
    exif = ctx.service("exiftool", ExifTool)
    data: dict[str, Any] = {}
    if exif.available:
        try:
            data = exif.read(path)
        except ExifToolError as exc:
            ctx.db.log_error("meta", str(path), str(exc))
    if not data and row["media_type"] in ("image", "heic"):
        data = et.pillow_exif(path)
    if data:
        et.write_cached(ctx.exif_dir, row["sha1"], data)
    return data


def _is_animated(path: Path) -> bool:
    if path.suffix.lower() not in ANIMATED_EXT:
        return False
    try:
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1) > 1
    except Exception:
        return False


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            width, height = img.size
            if img.getexif().get(274, 1) in (5, 6, 7, 8):
                width, height = height, width
            return width, height
    except Exception:
        return 0, 0


def _apply_meta(conn: sqlite3.Connection, photo_id: int, meta: MetaData, src_text: int) -> None:
    if meta.taken:
        sources.set_taken(conn, photo_id, meta.taken.utc, meta.taken.local, meta.taken.tz, meta.taken_src or sources.AUTO)
    sources.set_text(conn, photo_id, "title", meta.title, src_text)
    sources.set_text(conn, photo_id, "caption", meta.caption, src_text)
    if meta.keywords:
        tags.add_keywords(conn, photo_id, meta.keywords, src_text)
    if meta.uuid:
        conn.execute("UPDATE photos SET uuid=? WHERE id=?", (meta.uuid, photo_id))
    if meta.rating:
        conn.execute("UPDATE photos SET rating=? WHERE id=?", (meta.rating, photo_id))


def _apply_camera(conn: sqlite3.Connection, photo_id: int, meta: MetaData) -> None:
    conn.execute(
        "UPDATE photos SET camera_id=COALESCE(?, camera_id), lens_id=COALESCE(?, lens_id), "
        "camera_serial=CASE WHEN ?!='' THEN ? ELSE camera_serial END, iso=COALESCE(?, iso), "
        "exposure=CASE WHEN ?!='' THEN ? ELSE exposure END, f_number=COALESCE(?, f_number), "
        "focal_length=COALESCE(?, focal_length) WHERE id=?",
        (upsert_camera(conn, meta.make, meta.model), upsert_lens(conn, meta.lens), meta.serial, meta.serial, meta.iso,
         meta.exposure, meta.exposure, meta.f_number, meta.focal_length, photo_id),
    )
    if meta.lat is not None:
        conn.execute("UPDATE photos SET lat=?, lng=?, altitude=? WHERE id=?", (meta.lat, meta.lng, meta.altitude, photo_id))


def _apply_regions(conn: sqlite3.Connection, photo_id: int, file_id: int, meta: MetaData, width: int, height: int) -> None:
    """Volti indicati nel sidecar XMP (MWG-RS): diventano marker con nome, senza embedding."""
    conn.execute("DELETE FROM markers WHERE photo_id=? AND src=?", (photo_id, sources.XMP))
    for region in meta.regions:
        subj = ensure_subject(conn, region.name) if region.name else None
        conn.execute(
            "INSERT INTO markers(uid, file_id, photo_id, src, name, subj_id, x, y, w, h, size, score) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,100)",
            (new_uid("m"), file_id, photo_id, sources.XMP, region.name, subj, region.x, region.y, region.w, region.h,
             int(region.w * width)),
        )


def _sidecar_meta(ctx: Context, files: list[sqlite3.Row]) -> tuple[list[tuple[sqlite3.Row, MetaData]], list[takeout.TakeoutData]]:
    xmps: list[tuple[sqlite3.Row, MetaData]] = []
    takeouts: list[takeout.TakeoutData] = []
    for row in files:
        if row["media_type"] != "sidecar":
            continue
        ext = Path(row["name"]).suffix.lower()
        if ext == ".xmp":
            data = read_exif(ctx, row)
            if data:
                xmps.append((row, extract.extract(data, row["name"])))
        elif ext == ".json":
            parsed = takeout.parse(abs_path(row))
            if parsed:
                takeouts.append(parsed)
    return xmps, takeouts


def _probe_videos(ctx: Context, files: list[sqlite3.Row]) -> dict[int, probe.VideoInfo]:
    infos: dict[int, probe.VideoInfo] = {}
    for row in files:
        if row["media_type"] != "video":
            continue
        info = probe.probe(abs_path(row))
        if info is None:
            continue
        infos[row["id"]] = info
        ctx.db.conn().execute(
            "UPDATE files SET width=?, height=?, duration=?, fps=?, codec=? WHERE id=?",
            (info.width, info.height, info.duration, info.fps, info.vcodec, row["id"]),
        )
    return infos


def process_photo(ctx: Context, photo_id: int) -> bool:
    """Elabora una foto. Ritorna False se non c'è nulla da elaborare o il file principale è illeggibile."""
    db = ctx.db
    main = primary_file(db, photo_id)
    if main is None:
        return False
    first_time = db.scalar("SELECT meta_at FROM photos WHERE id=?", (photo_id,)) is None
    files = photo_files(db, photo_id)
    videos = _probe_videos(ctx, files)
    meta = extract.extract(read_exif(ctx, main), main["name"], main["mod_time"])
    xmps, takeouts = _sidecar_meta(ctx, files)

    main_info = videos.get(main["id"])
    duration = main_info.duration if main_info else meta.duration
    source = ensure_source(ctx, main, duration)
    if source is None:
        db.log_error("process", str(abs_path(main)), "anteprima non disponibile (conversione fallita)")
        db.conn().execute("UPDATE files SET error='conversione fallita' WHERE id=?", (main["id"],))
    width, height = (main_info.width, main_info.height) if main_info else (meta.width, meta.height)
    if source is not None and not (width and height):
        width, height = _image_size(source)

    thumbs: dict[str, Path] = {}
    color = None
    if source is not None:
        try:
            thumbs = generate_set(ctx, main["sha1"], source)
            preview = thumbs.get("fit_720") or next(iter(thumbs.values()), None)
            if preview is not None:
                with Image.open(preview) as img:
                    color = colors.analyze(img)
        except Exception as exc:
            db.log_error("thumbs", str(abs_path(main)), f"{type(exc).__name__}: {exc}")

    kind = None
    still = main["media_type"] in ("image", "heic", "raw")
    if still:
        short_video = next((v for r in files if r["media_type"] == "video"
                            if (v := videos.get(r["id"])) and live.is_live_pair(v.duration)), None)
        if short_video is not None or (meta.motion and live.extract_motion_video(ctx, abs_path(main), main["sha1"], meta.motion)):
            kind = "live"
        elif meta.animated or _is_animated(abs_path(main)):
            kind = "animated"
    if main["media_type"] == "video" and main_info and not transcode.is_web_playable(main_info, Path(main["name"]).suffix):
        from mosaic.jobs.queue import JobQueue
        JobQueue(db).enqueue("transcode", {"photo_id": photo_id}, unique=True)

    with db.tx() as conn:
        if main["media_type"] == "video" or still:
            sub = {"width": width, "height": height}
            conn.execute("UPDATE photos SET width=?, height=?, duration=? WHERE id=?",
                         (sub["width"], sub["height"], duration or 0.0, photo_id))
        for takeout_data in takeouts:
            if takeout_data.taken:
                sources.set_taken(conn, photo_id, takeout_data.taken.utc, takeout_data.taken.local,
                                  takeout_data.taken.tz, sources.META)
            if takeout_data.lat is not None:
                conn.execute("UPDATE photos SET lat=COALESCE(lat, ?), lng=COALESCE(lng, ?), altitude=COALESCE(altitude, ?) "
                             "WHERE id=?", (takeout_data.lat, takeout_data.lng, takeout_data.altitude, photo_id))
            sources.set_text(conn, photo_id, "title", takeout_data.title, sources.META)
            sources.set_text(conn, photo_id, "caption", takeout_data.caption, sources.META)
        _apply_meta(conn, photo_id, meta, sources.META)
        _apply_camera(conn, photo_id, meta)
        for _row, xmp_meta in xmps:
            _apply_meta(conn, photo_id, xmp_meta, sources.XMP)
            if xmp_meta.regions and width:
                _apply_regions(conn, photo_id, main["id"], xmp_meta, width, height)
        conn.execute("UPDATE files SET width=?, height=?, orientation=?, codec=CASE WHEN ?!='' THEN ? ELSE codec END "
                     "WHERE id=?", (width, height, meta.orientation, meta.codec, meta.codec, main["id"]))
        if color is not None:
            conn.execute(
                "UPDATE photos SET main_color=?, colors=?, luminance=?, chroma=? WHERE id=?",
                (color.main, " ".join(color.names), color.luminance, color.chroma, photo_id),
            )
        if kind:
            conn.execute("UPDATE photos SET type=? WHERE id=?", (kind, photo_id))
        conn.execute("UPDATE photos SET meta_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), photo_id))
        survivor = stack_photo(conn, photo_id, ctx.cfg.stack_uuid, ctx.cfg.stack_meta)
        refresh_primary(conn, survivor)
        quality.update(conn, survivor, has_thumbs=bool(thumbs))
        fts.reindex(conn, survivor)
    if first_time:  # foto mai elaborata: se esiste un backup YAML dei dati manuali, si riapplica
        from mosaic.organize import backup

        try:
            backup.restore(ctx, survivor)
        except Exception as exc:  # un backup rovinato non deve impedire l'elaborazione
            db.log_error("backup", str(survivor), f"{type(exc).__name__}: {exc}")
    return True


def thumb_file(ctx: Context, sha1: str, size_name: str) -> Path:
    return thumb_path(ctx.thumbs_dir, sha1, get_size(size_name))


def thumb_source(ctx: Context, photo_id: int, size_name: str = "fit_720") -> Path | None:
    """Miniatura esistente di una foto (usata dall'analisi AI); la crea se manca."""
    main = primary_file(ctx.db, photo_id)
    if main is None:
        return None
    existing = thumb_file(ctx, main["sha1"], size_name)
    if existing.exists():
        return existing
    source = ensure_source(ctx, main)
    if source is None:
        return None
    return generate_set(ctx, main["sha1"], source, (size_name,))[size_name]
