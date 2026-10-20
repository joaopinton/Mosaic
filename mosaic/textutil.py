"""Utilità di testo condivise."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str, max_len: int = 60) -> str:
    """'Ducati Panigale è rossa!' → 'ducati-panigale-e-rossa' (ASCII, minuscolo, senza simboli)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] if "-" in slug[:max_len] else slug[:max_len]
    return slug.strip("-")
