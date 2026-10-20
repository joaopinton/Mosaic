"""Rotte dell'esportazione delle foto in una cartella (D-30): job, cartella proposta, scelta della cartella, anteprima."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mosaic.api.deps import get_mosaic
from mosaic.api.schemas import EnhanceOptions, ExportPreview, ExportRequest, FolderPick
from mosaic.organize import enhance, export_photos
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")


def _enhance(body: EnhanceOptions | None) -> dict[str, Any] | None:
    return body.model_dump() if body is not None else None


@router.post("/exports", status_code=202)
def export_create(body: ExportRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    try:
        dest = export_photos.check_dest(m.ctx, body.dest)
    except export_photos.ExportError as exc:
        raise HTTPException(400, str(exc)) from exc
    marks = ",".join("?" * len(body.uids))
    if m.ctx.db.one(f"SELECT 1 FROM photos WHERE uid IN ({marks}) LIMIT 1", tuple(body.uids)) is None:
        raise HTTPException(404, "nessuna foto trovata")
    payload = {"uids": body.uids, "dest": str(dest), "raw": body.raw, "videos": body.videos, "enhance": _enhance(body.enhance)}
    return {"job": m.runner.submit("export", payload), "folder": str(dest)}


@router.get("/exports/default-dir")
def export_default_dir(title: str = "") -> dict[str, str]:
    """La cartella proposta: Scrivania\\<titolo>."""
    return {"folder": str(export_photos.default_dir(title[:200]))}


@router.post("/exports/preview")
def export_preview(body: ExportPreview, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Prima e dopo di qualche foto con «Migliora luce e colori», deciso su tutto il gruppo."""
    opts = enhance.Options(**body.enhance.model_dump())
    return {"photos": export_photos.preview(m.ctx, body.uids, opts)}


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
