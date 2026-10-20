"""Rotte del poster a mosaico (D-26): impaginazione per l'anteprima, creazione in un job, poster di ogni album (D-27)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from mosaic.api.deps import get_mosaic
from mosaic.api.schemas import PosterRequest
from mosaic.organize import albums, poster
from mosaic.service import Mosaic

router = APIRouter(prefix="/api")


def _options(body: PosterRequest) -> poster.Options:
    try:
        return poster.Options(paper=body.paper, landscape=body.landscape, style=body.style, line=body.line,
                              colors=body.colors, seed=body.seed, dpi=body.dpi, occasion=body.occasion,
                              date=body.date, place=body.place)
    except poster.PosterError as exc:
        raise HTTPException(400, str(exc)) from exc


def _album(m: Mosaic, ref: str) -> Any:
    album = albums.get_album(m.ctx.db.conn(), ref)
    if album is None or album["deleted_at"]:
        raise HTTPException(404, "album non trovato")
    return album


@router.post("/posters/layout")
def poster_layout(body: PosterRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """La stessa impaginazione del file finale, in coordinate 0..1, per l'anteprima dal vivo."""
    opts = _options(body)
    try:
        lay = poster.plan(m.ctx, body.uids, opts)
    except poster.PosterError as exc:
        raise HTTPException(400, str(exc)) from exc
    ids = [c.piece.photo_id for c in lay.cells if c.piece.photo_id is not None]
    rows = {r["id"]: r for r in m.ctx.db.all(
        f"SELECT p.id, p.uid, f.sha1 FROM photos p LEFT JOIN files f ON f.photo_id=p.id AND f.is_primary=1 "
        f"WHERE p.id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
    px_w, px_h, dpi = poster.pixel_size(opts)
    line = poster.line_px(opts, px_w, px_h)
    cells = [_cell(c, lay, rows, px_w / lay.width, line) for c in lay.cells]
    facts = poster.facts_for(m.ctx, ids, opts)
    return {"width_mm": lay.width, "height_mm": lay.height, "pixels": [px_w, px_h], "dpi": dpi,
            "line": 0 if opts.style == "none" else poster.LINES_MM[opts.line], "ink": poster.INK[opts.style], "cells": cells,
            "facts": [{"label": f.label, "value": f.value} for f in facts]}


def _cell(c: poster.Cell, lay: poster.Layout, rows: dict, scale: float, line: int) -> dict[str, Any]:
    """Un riquadro per l'anteprima; per quelli con i dati, testo e misure in frazioni dell'altezza del riquadro."""
    out: dict[str, Any] = {
        "x": c.x / lay.width, "y": c.y / lay.height, "w": c.w / lay.width, "h": c.h / lay.height,
        "uid": rows[c.piece.photo_id]["uid"] if c.piece.photo_id in rows else None,
        "hash": rows[c.piece.photo_id]["sha1"] if c.piece.photo_id in rows else None,
        "color": c.fill or (c.piece.color if c.piece.color != poster.TEXT else None), "crop": list(c.crop), "text": None}
    if c.fact and c.fit:
        _, y0, _, y1 = poster.pixel_box(c, lay, scale, line)
        height = max(1, y1 - y0)
        out["text"] = {"fg": c.fg, "label": c.fact.label.upper(), "lines": list(c.fit.lines),
                       "value": c.fit.value_px / height, "label_size": c.fit.label_px / height, "pad": c.fit.pad_px / height}
    return out


@router.post("/posters", status_code=202)
def poster_create(body: PosterRequest, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    """Crea il file in un job (progresso riquadro per riquadro), nella cartella dei poster dell'album (D-27)."""
    if not body.album:
        raise HTTPException(400, "manca l'album di riferimento")
    album = _album(m, body.album)
    opts = _options(body)
    folder = poster.album_dir(m.ctx, album)
    name = poster.poster_name(body.title or album["title"], datetime.now())
    payload = {"uids": body.uids, "name": name, "dir": str(folder), "options": {
        "paper": opts.paper, "landscape": opts.landscape, "style": opts.style, "line": opts.line, "colors": opts.colors,
        "seed": opts.seed, "dpi": opts.dpi, "occasion": opts.occasion, "date": opts.date, "place": opts.place}}
    return {"job": m.runner.submit("poster", payload), "name": name, "folder": str(folder)}


@router.get("/albums/{ref}/posters")
def album_posters(ref: str, m: Mosaic = Depends(get_mosaic)) -> dict[str, Any]:
    folder = poster.album_dir(m.ctx, _album(m, ref))
    return {"folder": str(folder), "posters": [
        {"name": f.name, "size": f.stat().st_size, "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
        for f in poster.list_posters(folder)[:50]]}


def _existing(m: Mosaic, ref: str, name: str) -> Path:
    try:
        path = poster.poster_path(poster.album_dir(m.ctx, _album(m, ref)), name)
    except poster.PosterError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "poster non trovato")
    return path


@router.get("/albums/{ref}/posters/{name}")
def poster_file(ref: str, name: str, m: Mosaic = Depends(get_mosaic)) -> FileResponse:
    return FileResponse(_existing(m, ref, name), media_type="image/jpeg", filename=name)


@router.post("/albums/{ref}/posters/{name}/reveal", status_code=204)
def poster_reveal(ref: str, name: str, m: Mosaic = Depends(get_mosaic)) -> None:
    """Apre Esplora file sulla cartella con il poster selezionato (solo Windows)."""
    path = _existing(m, ref, name)
    if os.name != "nt":
        raise HTTPException(501, "disponibile solo su Windows")
    # explorer legge da sé la riga di comando: «/select,"percorso"» tutto attaccato, altrimenti apre Documenti.
    # Il nome è validato da NAME_RE (niente virgolette) e la cartella la decide Mosaic.
    subprocess.Popen(f'explorer /select,"{path}"')  # noqa: S603


@router.post("/albums/{ref}/posters/{name}/open", status_code=204)
def poster_open(ref: str, name: str, m: Mosaic = Depends(get_mosaic)) -> None:
    """Apre il poster con il programma predefinito per le immagini (solo Windows)."""
    path = _existing(m, ref, name)
    if os.name != "nt":
        raise HTTPException(501, "disponibile solo su Windows")
    os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
