"""Applicazione FastAPI: API REST + interfaccia Vue compilata, solo su localhost."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mosaic.api import routes_collections, routes_drop, routes_export, routes_files, routes_library, routes_photos, routes_poster
from mosaic.paths import FRONTEND_DIST
from mosaic.service import Mosaic

log = logging.getLogger(__name__)

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
NO_UI = """<!doctype html><meta charset="utf-8"><title>Mosaic</title>
<body style="font:16px system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>Mosaic è in funzione</h1><p>L'interfaccia non è stata compilata (<code>frontend/dist</code> manca).
L'API è comunque attiva su <a href="/api/status">/api/status</a>.</p></body>"""


def _hostname(value: str) -> str:
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.split(":")[0].lower()


def create_app(m: Mosaic, allowed_hosts: set[str] | None = None, dist: Path | None = None,
               start_workers: bool = True) -> FastAPI:
    allowed = LOOPBACK | {h.lower() for h in (allowed_hosts or set())}
    dist = dist if dist is not None else FRONTEND_DIST

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if start_workers:
            m.start()
            if m.cfg.library_roots and m.ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE state='queued'") == 0:
                m.runner.submit("index", {}, unique=True)  # all'avvio la libreria si rincontrolla da sola (incrementale)
        yield
        if start_workers:
            m.stop()

    app = FastAPI(title="Mosaic", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.mosaic = m

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Difende da DNS rebinding (Host) e CSRF (Origin) sulle richieste che modificano dati."""
        if _hostname(request.headers.get("host", "")) not in allowed:
            return JSONResponse({"detail": "host non consentito"}, status_code=400)
        if request.method in UNSAFE:
            origin = request.headers.get("origin")
            if origin and _hostname(urlsplit(origin).netloc) not in allowed:
                return JSONResponse({"detail": "origine non consentita"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "richiesta tra siti non consentita"}, status_code=403)
        response = await call_next(request)
        if (request.method in UNSAFE and response.status_code < 400 and request.url.path.startswith("/api/")
                and not request.url.path.startswith(("/api/drop/", "/api/posters", "/api/exports", "/api/dialog/"))
                and not request.url.path.endswith(("/reveal", "/open"))):
            m.runner.submit("backup", {}, unique=True)  # ogni modifica manuale finisce nel backup YAML
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    for router in (routes_photos.router, routes_collections.router, routes_library.router, routes_files.router,
                   routes_drop.router, routes_poster.router, routes_export.router):
        app.include_router(router)

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str):  # type: ignore[no-untyped-def]
        if path.startswith(("api/", "t/")):
            return JSONResponse({"detail": "non trovato"}, status_code=404)
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(NO_UI)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
