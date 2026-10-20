"""Dipendenze condivise dalle rotte dell'API."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, Request

from mosaic.service import Mosaic

PUBLIC_FIELDS = ("uid", "type", "title", "caption", "taken_at_local", "width", "height", "duration", "main_color",
                 "quality", "hash")


def get_mosaic(request: Request) -> Mosaic:
    return request.app.state.mosaic


def photo_or_404(m: Mosaic, uid: str) -> sqlite3.Row:
    row = m.ctx.db.one("SELECT * FROM photos WHERE uid=?", (uid,))
    if row is None:
        raise HTTPException(404, "foto non trovata")
    return row


def summary(p: dict[str, Any]) -> dict[str, Any]:
    """Forma pubblica di una foto negli elenchi."""
    out = {k: p.get(k) for k in PUBLIC_FIELDS}
    out["favorite"] = bool(p.get("favorite"))
    out["private"] = bool(p.get("private"))
    out["archived"] = p.get("deleted_at") is not None
    return out
