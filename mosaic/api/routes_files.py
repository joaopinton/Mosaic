"""Rotte dei file: miniature, originali, video (con Range), Live Photo e ritagli dei volti.

Nessuna rotta accetta percorsi: i file si raggiungono solo tramite hash o uid presenti nel database,
e gli originali vengono serviti solo se stanno dentro una cartella della libreria.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

from mosaic.api.deps import get_mosaic, photo_or_404
from mosaic.index.store import abs_path, photo_files, primary_file
from mosaic.service import Mosaic
from mosaic.thumbs.convert import ensure_source
from mosaic.thumbs.render import ensure_thumb, thumb_path, valid_size
from mosaic.thumbs.sizes import get as get_size
from mosaic.video import live, probe, transcode

router = APIRouter()
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
CROP_SIZES = (64, 96, 128, 160, 256)


def _inside_library(m: Mosaic, path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(Path(root).resolve()) for root in m.cfg.library_roots) or any(
        resolved.is_relative_to(Path(r["root"]).resolve()) for r in m.ctx.db.all("SELECT DISTINCT root FROM files"))


@router.get("/t/{sha1}/{size}")
def thumbnail(sha1: str, size: str, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    if not SHA1_RE.match(sha1) or not valid_size(size):
        raise HTTPException(404, "miniatura non valida")
    existing = thumb_path(m.ctx.thumbs_dir, sha1, get_size(size))
    if existing.exists():
        return FileResponse(existing, media_type="image/jpeg", headers=IMMUTABLE)
    row = m.ctx.db.one("SELECT * FROM files WHERE sha1=? AND missing=0 AND media_type!='sidecar' LIMIT 1", (sha1,))
    if row is None:
        raise HTTPException(404, "file sconosciuto")
    source = ensure_source(m.ctx, row, row["duration"] or 0.0)
    if source is None:
        raise HTTPException(404, "anteprima non disponibile")
    return FileResponse(ensure_thumb(m.ctx, sha1, source, size), media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/api/files/{uid}/original")
def original(uid: str, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    row = primary_file(m.ctx.db, photo_or_404(m, uid)["id"])
    if row is None:
        raise HTTPException(404, "file non presente")
    path = abs_path(row)
    if not path.is_file() or not _inside_library(m, path):
        raise HTTPException(404, "file non presente")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=row["name"], content_disposition_type="inline")


@router.get("/api/videos/{uid}", response_model=None)
def video(uid: str, m: Mosaic = Depends(get_mosaic)) -> FileResponse | JSONResponse:
    """Video riproducibile: l'originale se il browser lo supporta, altrimenti la versione transcodificata."""
    photo = photo_or_404(m, uid)
    row = primary_file(m.ctx.db, photo["id"])
    if row is None or row["media_type"] != "video":
        raise HTTPException(404, "non è un video")
    path = abs_path(row)
    if not path.is_file() or not _inside_library(m, path):
        raise HTTPException(404, "file non presente")
    ready = transcode.transcoded_path(m.ctx, row["sha1"])
    if ready.exists():
        return FileResponse(ready, media_type="video/mp4")
    info = probe.probe(path)
    if info is None or transcode.is_web_playable(info, path.suffix):
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "video/mp4")
    m.runner.submit("transcode", {"photo_id": photo["id"]}, unique=True)
    return JSONResponse({"status": "transcoding"}, status_code=202)


@router.get("/api/live/{uid}")
def live_video(uid: str, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    """Breve video di una Live Photo: il file video dello stack oppure il Motion Photo estratto."""
    photo = photo_or_404(m, uid)
    if photo["type"] != "live":
        raise HTTPException(404, "non è una Live Photo")
    files = photo_files(m.ctx.db, photo["id"])
    for f in files:
        if f["media_type"] == "video" and 0 < f["duration"] <= live.LIVE_MAX_SECONDS and abs_path(f).is_file():
            return FileResponse(abs_path(f), media_type=mimetypes.guess_type(f["name"])[0] or "video/mp4")
    main = next((f for f in files if f["is_primary"]), None)
    if main is not None:
        extracted = live.motion_video_path(m.ctx, main["sha1"])
        if extracted.exists():
            return FileResponse(extracted, media_type="video/mp4")
    raise HTTPException(404, "video della Live Photo non disponibile")


@router.get("/api/markers/{uid}/crop")
def marker_crop(uid: str, size: int = 160, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    """Ritaglio del volto (con un po' di margine) dalla miniatura grande della foto."""
    if size not in CROP_SIZES:
        raise HTTPException(400, f"dimensione non valida: usa una tra {CROP_SIZES}")
    marker = m.ctx.db.one("SELECT m.*, f.sha1, f.id AS fid FROM markers m JOIN files f ON f.id=m.file_id WHERE m.uid=?", (uid,))
    if marker is None:
        raise HTTPException(404, "volto non trovato")
    dest = m.ctx.data_dir / "cache" / "crops" / f"{uid}_{size}.jpg"
    if not dest.exists():
        file_row = m.ctx.db.one("SELECT * FROM files WHERE id=?", (marker["fid"],))
        source = ensure_source(m.ctx, file_row)
        if source is None:
            raise HTTPException(404, "anteprima non disponibile")
        big = ensure_thumb(m.ctx, marker["sha1"], source, "fit_1920")
        with Image.open(big) as img:
            img.load()
            w, h = img.size
            margin = 0.35
            cx, cy = (marker["x"] + marker["w"] / 2) * w, (marker["y"] + marker["h"] / 2) * h
            half = max(marker["w"] * w, marker["h"] * h) * (0.5 + margin)
            box = (max(0, int(cx - half)), max(0, int(cy - half)), min(w, int(cx + half)), min(h, int(cy + half)))
            crop = img.convert("RGB").crop(box).resize((size, size), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, "JPEG", quality=85)
    return FileResponse(dest, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
