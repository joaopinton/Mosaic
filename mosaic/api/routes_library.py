"""Rotte della libreria: stato, configurazione, job ed eventi, indicizzazione, import, rinomina, esportazione."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from mosaic import __version__
from mosaic import config as config_mod
from mosaic.api.deps import get_mosaic
from mosaic.api.schemas import (
    CaptionRequest,
    CleanupRequest,
    CloudKey,
    ConfigPatch,
    ImportRequest,
    IndexRequest,
    RenameRequest,
    RestoreExcluded,
    RollbackRequest,
    UnlinkRoot,
)
from mosaic.assets import status as assets_status
from mosaic.jobs.events import EventBus
from mosaic.llm import secrets
from mosaic.llm.backends import NO_CAPTIONER, PROVIDERS, captioning_available
from mosaic.organize import catalog, rename
from mosaic.organize.export import export_catalog
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")
HEARTBEAT = 15.0
PLAN_PREVIEW = 300


@router.get("/status")
def status(m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    db = m.ctx.db
    assets = assets_status()
    return {
        "version": __version__,
        "first_run": not m.cfg.library_roots,
        "library_roots": list(m.cfg.library_roots),
        "photos": db.scalar("SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL"),
        "videos": db.scalar("SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL AND type='video'"),
        "analyzed": db.scalar("SELECT COUNT(*) FROM photos WHERE analyzed_at IS NOT NULL"),
        "people": db.scalar("SELECT COUNT(*) FROM subjects WHERE photo_count>0"),
        "albums": db.scalar("SELECT COUNT(*) FROM albums WHERE deleted_at IS NULL AND photo_count>0"),
        "jobs": m.runner.queue.counts(),
        "busy": m.runner.queue.pending() > 0,
        "assets": assets,
        "ai_ready": assets.get("siglip2", False),
        "llm_ready": captioning_available(m.ctx),
        "caption_auto": m.cfg.llm.mode == "newly-indexed",  # le descrizioni sono un passo della pipeline
        "rename_auto": m.cfg.rename.auto,
        "delete_mode": m.cfg.delete_mode,
    }


@router.get("/config")
def get_config(m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    return config_mod.to_dict(m.cfg)


@router.put("/config")
def put_config(body: ConfigPatch, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    changes = body.changes
    for root in changes.get("library_roots", []):
        if not isinstance(root, str) or not Path(root).is_dir():
            raise HTTPException(400, f"cartella non trovata: {root}")
    try:
        new = config_mod.update(m.cfg, changes)
    except (config_mod.ConfigError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    m.reload_config(new)
    return config_mod.to_dict(new)


# --- didascalie nel cloud (facoltative) --------------------------------------------------------------------

@router.get("/llm/cloud")
def cloud_status(m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Servizi cloud per le didascalie: da dove viene la chiave ("file", "env" o null). La chiave non esce mai."""
    cfg = m.cfg.llm.cloud
    keys = secrets.key_status(m.ctx.data_dir)
    return {"providers": {name: {"label": spec.label, "key": keys[name], "model": getattr(cfg, f"{name}_model")}
                          for name, spec in PROVIDERS.items()}}


@router.put("/llm/cloud/key")
def put_cloud_key(body: CloudKey, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Salva (o con chiave vuota cancella) la chiave di un servizio, in data/secrets.yaml."""
    try:
        secrets.set_key(m.ctx.data_dir, body.provider, body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return cloud_status(m)


# --- job ed eventi ------------------------------------------------------------------------------

def _job_total(row: Any) -> int:
    """Totale noto del job; per quelli ancora in coda con un elenco di foto, la lunghezza dell'elenco."""
    if row["total"]:
        return int(row["total"])
    try:
        ids = json.loads(row["payload"] or "{}").get("ids")
    except ValueError:
        return 0
    return len(ids) if isinstance(ids, list) else 0


@router.get("/jobs")
def jobs(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    return [{"id": r["id"], "type": r["type"], "state": r["state"], "progress": r["progress"], "message": r["message"],
             "error": r["error"], "created_at": r["created_at"], "started_at": r["started_at"],
             "finished_at": r["finished_at"], "done": r["done"], "total": _job_total(r)}
            for r in m.runner.queue.list(40)]


@router.post("/jobs/cancel-type/{job_type}")
def cancel_job_type(job_type: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    if job_type not in m.runner.handlers:
        raise HTTPException(400, f"tipo di attività sconosciuto: {job_type}")
    return {"cancelled": m.runner.cancel_type(job_type)}


@router.post("/jobs/restart-type/{job_type}")
def restart_job_type(job_type: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    try:
        return {"job": m.runner.restart_type(job_type)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, m: Mosaic = Depends(get_mosaic)) -> dict[str, bool]:
    return {"cancelled": m.runner.cancel(job_id)}


async def event_stream(bus: EventBus, request: Request | None = None) -> AsyncIterator[str]:
    """Server-Sent Events: un evento per ogni cambio di stato dei job, con un battito per tenere aperta la connessione."""
    q = bus.subscribe()
    try:
        yield "retry: 3000\n\n"
        while request is None or not await request.is_disconnected():
            try:
                event = await asyncio.to_thread(q.get, True, HEARTBEAT)
            except Exception:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        bus.unsubscribe(q)


@router.get("/events")
async def events(request: Request, m: Mosaic = Depends(get_mosaic)) -> StreamingResponse:
    return StreamingResponse(event_stream(m.bus, request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- azioni sulla libreria ----------------------------------------------------------------------------

def _submit(m: Mosaic, job_type: str, payload: dict[str, Any] | None = None) -> dict[str, int]:
    return {"job": m.runner.submit(job_type, payload or {}, unique=True)}


@router.post("/library/index", status_code=202)
def library_index(body: IndexRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    if not m.cfg.library_roots:
        raise HTTPException(400, "nessuna cartella della libreria configurata")
    return _submit(m, "index", {"rescan": body.rescan})


@router.post("/library/import", status_code=202)
def library_import(body: ImportRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    if not Path(body.source).is_dir():
        raise HTTPException(400, f"cartella non trovata: {body.source}")
    if not m.cfg.library_roots:
        raise HTTPException(400, "nessuna cartella della libreria configurata")
    return _submit(m, "import", {"source": body.source, "move": body.move})


@router.get("/library/roots")
def library_roots(m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Cartelle della libreria con il numero di foto, quelle rimaste nel catalogo senza essere più configurate e quelle scollegate."""
    db = m.ctx.db
    configured = list(m.cfg.library_roots)
    counts = {r["root"]: r["n"] for r in db.all("SELECT root, COUNT(DISTINCT photo_id) AS n FROM files GROUP BY root")}

    def photos(path: str) -> int:
        return sum(n for root, n in counts.items() if catalog.same_root(root, path))

    stale = [r for r in catalog.catalog_roots(m.ctx) if not any(catalog.same_root(r, c) for c in configured)]
    return {"roots": [{"path": r, "linked": True, "photos": photos(r)} for r in configured]
                     + [{"path": r, "linked": False, "photos": photos(r)} for r in stale],
            "unlinked": catalog.unlinked_roots(m.ctx)}


@router.post("/library/roots/unlink", status_code=202)
def library_unlink(body: UnlinkRoot, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    """Scollega una cartella: via dalla configurazione subito, via dal catalogo con un job. I file su disco restano."""
    configured = [r for r in m.cfg.library_roots if catalog.same_root(r, body.root)]
    if not configured and not any(catalog.same_root(r, body.root) for r in catalog.catalog_roots(m.ctx)):
        raise HTTPException(400, f"cartella sconosciuta: {body.root}")
    if configured:
        remaining = [r for r in m.cfg.library_roots if r not in configured]
        m.reload_config(config_mod.update(m.cfg, {"library_roots": remaining}))
    return {"job": m.runner.submit("unlink", {"root": body.root})}


@router.get("/excluded")
def excluded(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    return catalog.excluded_list(m.ctx)


@router.post("/excluded/restore", status_code=202)
def excluded_restore(body: RestoreExcluded, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    """Rimette nel catalogo le foto tolte: si cancella l'esclusione e si rilancia l'indicizzazione."""
    n = catalog.restore_excluded(m.ctx, body.sha1s)
    return {"restored": n, **(_submit(m, "index", {"rescan": False}) if m.cfg.library_roots else {"job": 0})}


@router.post("/library/cleanup", status_code=202)
def library_cleanup(body: CleanupRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    return _submit(m, "cleanup", {"purge": body.purge})


@router.post("/library/analyze", status_code=202)
def library_analyze(m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    return _submit(m, "analyze")


@router.post("/library/caption", status_code=202)
def library_caption(body: CaptionRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    if not captioning_available(m.ctx):
        raise HTTPException(400, NO_CAPTIONER)
    ids: list[int] | None = None
    if body.uids:
        ids = [r["id"] for r in m.ctx.db.all(
            f"SELECT id FROM photos WHERE uid IN ({','.join('?' * len(body.uids))})", tuple(body.uids))]
    return _submit(m, "caption", {"ids": ids, "force": body.force} if ids else {"force": body.force})


# --- rinomina ---------------------------------------------------------------------------------------------

@router.post("/rename/preview")
def rename_preview(body: RenameRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    ids = None
    if body.uids is not None:
        ids = [r["id"] for r in m.ctx.db.all(
            f"SELECT id FROM photos WHERE uid IN ({','.join('?' * len(body.uids))})", tuple(body.uids))] if body.uids else []
    try:
        plan = rename.plan_rename(m.ctx, ids, body.force, body.pattern)
    except (KeyError, ValueError, IndexError) as exc:
        raise HTTPException(400, f"schema non valido: {exc}") from exc
    return {"photos": plan.photos, "files": len(plan.ops), "skipped": len(plan.skipped),
            "ops": [{"dir": op.dir, "from": op.old_name, "to": op.new_name} for op in plan.ops[:PLAN_PREVIEW]]}


@router.post("/rename/apply", status_code=202)
def rename_apply(body: RenameRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    payload: dict[str, Any] = {"apply": True, "force": body.force, "pattern": body.pattern}
    if body.uids is not None:
        payload["ids"] = [r["id"] for r in m.ctx.db.all(
            f"SELECT id FROM photos WHERE uid IN ({','.join('?' * len(body.uids))})", tuple(body.uids))] if body.uids else []
    return _submit(m, "rename", payload)


@router.post("/rename/rollback", status_code=202)
def rename_rollback(body: RollbackRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, int]:
    if not body.batch_id and not body.all:
        raise HTTPException(400, "indica batch_id oppure all")
    return _submit(m, "rollback", {"batch_id": None if body.all else body.batch_id})


@router.get("/rename/batches")
def rename_batches(m: Mosaic = Depends(get_mosaic)) -> list[dict[str, Any]]:
    rows = m.ctx.db.all(
        "SELECT batch_id, MIN(ts) AS ts, COUNT(*) AS files, COUNT(DISTINCT photo_id) AS photos, "
        "SUM(state='done') AS done, SUM(state='rolled_back') AS rolled_back, SUM(state='failed') AS failed "
        "FROM rename_journal GROUP BY batch_id ORDER BY MIN(id) DESC LIMIT 50")
    return [dict(r) for r in rows]


# --- esportazione e asset ---------------------------------------------------------------------------------

@router.get("/export")
def export(format: str = "json", private: bool = False, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    if format not in ("json", "csv"):
        raise HTTPException(400, "formato non supportato")
    tmp = Path(tempfile.mkdtemp(prefix="mosaic-export-")) / f"catalogo.{format}"
    export_catalog(m.ctx, format, tmp, private)
    return FileResponse(tmp, filename=tmp.name, media_type="text/csv" if format == "csv" else "application/json",
                        background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))


@router.get("/assets")
def assets() -> dict[str, bool]:
    return assets_status()
