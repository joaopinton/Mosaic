"""Rotte del trascina e rilascia (D-25): anteprima di cosa è stato rilasciato, aggiunta alla libreria, caricamento dal browser."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mosaic import config as config_mod
from mosaic.api.deps import get_mosaic
from mosaic.api.schemas import DropAdd, DropPaths
from mosaic.organize import dropin
from mosaic.service import Mosaic

router = APIRouter(prefix="/api/drop")

MAX_UPLOAD = 16 * 1024 ** 3   # un solo file caricato: al massimo 16 GB
UPLOAD_MARGIN = 256 * 1024 ** 2
CHUNK = 1 << 20


def _item(i: dropin.DropItem) -> dict[str, Any]:
    return {"path": i.path, "name": i.name, "kind": i.kind, "size": i.size, "media": i.media,
            "in_library": i.in_library, "contains_library": i.contains_library, "folders": i.folders, "zips": i.zips,
            "dest_name": i.dest_name}


@router.post("/inspect")
def drop_inspect(body: DropPaths, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Cosa è stato rilasciato e dove andranno le copie (la prima cartella della libreria)."""
    items = dropin.inspect(m.ctx, body.paths)
    return {"items": [_item(i) for i in items], "dest": m.cfg.library_roots[0] if m.cfg.library_roots else None}


@router.post("/add", status_code=202)
def drop_add(body: DropAdd, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Collega le cartelle dove sono (o le copia), copia foto e video, estrae gli zip. Tutto in job con il loro progresso.

    Gli zip dentro le cartelle collegate (o già nella libreria) si estraggono accanto a sé, in una cartella con il loro
    nome, nel job `drop` accodato prima dell'indicizzazione: così le foto estratte entrano già al primo giro (D-31).
    """
    items = dropin.inspect(m.ctx, body.paths)
    folders = [i for i in items if i.kind == "folder" and not i.in_library]
    link = _outermost([i.path for i in folders if body.folders == "link" and not i.contains_library])
    copy = [i.path for i in folders if body.folders == "copy" or i.contains_library]
    files = [i.path for i in items if i.kind == "media" and not i.in_library]
    zips = [i.path for i in items if i.kind == "zip"]
    reindex = any(i.in_library and i.kind in ("folder", "media") for i in items)
    in_place = _outermost([*link, *(i.path for i in items if i.kind == "folder" and i.in_library)])
    unzip = [str(z) for p in in_place for z in dropin.nested_zips(Path(p), (m.ctx.data_dir,))]
    if not (link or copy or files or zips or reindex):
        raise HTTPException(400, "niente da aggiungere: rilascia foto, video, cartelle o file .zip")
    if (copy or files or zips) and not (m.cfg.library_roots or link):
        raise HTTPException(400, "collega prima una cartella alla libreria: le copie vanno lì")
    out: dict[str, Any] = {"linked": link, "jobs": []}
    if link:
        m.reload_config(config_mod.update(m.cfg, {"library_roots": [*m.cfg.library_roots, *link]}))
    if copy or files or zips or unzip:  # i job pesanti girano uno alla volta, in ordine: prima si estrae
        out["jobs"].append(m.runner.submit("drop", {"files": files, "zips": zips, "copy_folders": copy, "unzip_here": unzip}))
    if link:
        out["jobs"].append(m.runner.submit("index", {"roots": link}))
    if reindex:
        out["jobs"].append(m.runner.submit("index", {}, unique=True))
    return out


def _outermost(paths: list[str]) -> list[str]:
    """Senza doppioni e senza cartelle già contenute in un'altra dello stesso elenco (niente radici annidate)."""
    chosen: list[Path] = []
    for p in sorted({Path(x) for x in paths}, key=lambda q: len(q.parts)):
        if not any(c == p or c in p.parents for c in chosen):
            chosen.append(p)
    return [str(p) for p in chosen]


@router.put("/upload/{batch}", status_code=201)
async def drop_upload(batch: str, path: str, request: Request, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Un file caricato dal browser (corpo grezzo, nessun multipart), dentro `data/cache/uploads/<lotto>`."""
    try:
        target = dropin.upload_target(m.ctx, batch, path)
    except dropin.DropError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(400, "lunghezza non valida") from exc
    if length > MAX_UPLOAD:
        raise HTTPException(413, "file troppo grande")
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < length + UPLOAD_MARGIN:
        raise HTTPException(507, "spazio su disco insufficiente per caricare il file")
    part = target.with_name(target.name + ".part")
    written = 0
    try:
        with part.open("wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_UPLOAD:
                    raise HTTPException(413, "file troppo grande")
                await asyncio.to_thread(out.write, chunk)
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"path": path, "size": written}


@router.post("/upload/{batch}/finish", status_code=202)
def drop_upload_finish(batch: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    try:
        folder = dropin.upload_dir(m.ctx, batch)
    except dropin.DropError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not folder.is_dir():
        raise HTTPException(400, "nessun file caricato in questo lotto")
    if not m.cfg.library_roots:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, "collega prima una cartella alla libreria: le copie vanno lì")
    return {"job": m.runner.submit("drop", {"upload_dir": str(folder)})}


@router.delete("/upload/{batch}", status_code=204)
def drop_upload_cancel(batch: str, m: Mosaic = Depends(get_mosaic)) -> None:
    try:
        shutil.rmtree(dropin.upload_dir(m.ctx, batch), ignore_errors=True)
    except dropin.DropError as exc:
        raise HTTPException(400, str(exc)) from exc
