"""Rotte dell'esportazione delle foto in una cartella (D-30): job, cartella proposta, scelta della cartella, anteprima."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from mosaic.api.deps import get_mosaic
from mosaic.api.schemas import CropFormat, EnhanceOptions, ExportCropPreview, ExportPreview, ExportRequest, FolderPick
from mosaic.organize import enhance, export_photos, smart_crop
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")


def _enhance(body: EnhanceOptions | None) -> dict[str, Any] | None:
    return body.model_dump() if body is not None else None


def _crop(body: CropFormat | None) -> dict[str, Any] | None:
    return body.model_dump() if body is not None else None


@router.post("/exports", status_code=202)
def export_create(body: ExportRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    try:
        dest = Path(export_photos.zip_dir(m.ctx)) if body.zip else export_photos.check_dest(m.ctx, body.dest)
    except export_photos.ExportError as exc:
        raise HTTPException(400, str(exc)) from exc
    marks = ",".join("?" * len(body.uids))
    if m.ctx.db.one(f"SELECT 1 FROM photos WHERE uid IN ({marks}) LIMIT 1", tuple(body.uids)) is None:
        raise HTTPException(404, "nessuna foto trovata")
    payload = {"uids": body.uids, "dest": str(dest), "raw": body.raw, "videos": body.videos, "enhance": _enhance(body.enhance),
               "crop": _crop(body.crop), "fmt": body.fmt, "quality": body.quality, "max_side": body.max_side,
               "numbered": body.numbered, "zip": body.zip, "zip_name": export_photos.folder_name(body.zip_name)}
    return {"job": m.runner.submit("export", payload), "folder": "" if body.zip else str(dest)}


@router.get("/exports/{job_id}/download")
def export_download(job_id: int, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    """Lo zip di un'esportazione «Scarica»: un vero download del browser (attachment)."""
    row = m.runner.queue.get(job_id)
    if row is None or row["type"] != "export":
        raise HTTPException(404, "esportazione non trovata")
    payload = json.loads(row["payload"])
    path = export_photos.zip_dir(m.ctx) / f"export-{job_id}.zip"
    if not payload.get("zip") or row["state"] != "done" or not path.is_file():
        raise HTTPException(404, "lo zip non è pronto o è scaduto")
    return FileResponse(path, media_type="application/zip", filename=f"{payload.get('zip_name') or 'Foto Mosaic'}.zip")


@router.get("/exports/default-dir")
def export_default_dir(title: str = "") -> dict[str, str]:
    """La cartella proposta: Scrivania\\<titolo>."""
    return {"folder": str(export_photos.default_dir(title[:200]))}


@router.post("/exports/preview")
def export_preview(body: ExportPreview, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Prima e dopo di qualche foto con «Migliora luce e colori», deciso su tutto il gruppo."""
    opts = enhance.Options(**body.enhance.model_dump())
    return {"photos": export_photos.preview(m.ctx, body.uids, opts)}


@router.post("/exports/crop-preview")
def export_crop_preview(body: ExportCropPreview, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Qualche foto con il riquadro di «Ritaglia a formato» (D-34), calcolato come per i file."""
    return {"photos": export_photos.crop_preview(m.ctx, body.uids, smart_crop.Format(**body.crop.model_dump()))}


@router.post("/exports/{job_id}/reveal", status_code=204)
def export_reveal(job_id: int, m: Mosaic = Depends(get_mosaic)) -> None:
    """Apre in Esplora file la cartella di un'esportazione (solo quella scritta nel job, mai un percorso qualsiasi)."""
    row = m.runner.queue.get(job_id)
    if row is None or row["type"] != "export":
        raise HTTPException(404, "esportazione non trovata")
    folder = Path(json.loads(row["payload"])["dest"])
    if not folder.is_dir():
        raise HTTPException(404, "la cartella non esiste più")
    if os.name != "nt":
        raise HTTPException(501, "disponibile solo su Windows")
    os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606


@router.post("/dialog/folder")
def dialog_folder(body: FolderPick) -> dict[str, str | None]:
    """Finestra di Windows per scegliere una cartella; 501 quando l'interfaccia non gira nell'app desktop."""
    from mosaic import desktop

    try:
        return {"folder": desktop.pick_folder(body.start)}
    except RuntimeError as exc:
        raise HTTPException(501, str(exc)) from exc
