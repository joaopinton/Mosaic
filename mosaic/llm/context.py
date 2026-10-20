"""Contesto per le didascalie: chi è l'utente, la giornata raccontata nell'album e il momento dello scatto."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mosaic.context import Context
from mosaic.organize.moments import MONTHS
from mosaic.search.parser import parse
from mosaic.search.query import build_where

MAX_ABOUT = 400    # caratteri: il contesto di 2048 token deve contenere anche immagine, prompt e risposta
MAX_STORY = 900
MAX_STORIES = 2    # album più specifico per primo (di solito la cartella, poi la cartella principale)

HEADER = {
    "it": "Contesto fornito dall'utente (usalo solo se coerente con l'immagine; non inventare altro):",
    "en": "Context provided by the user (use it only if it fits the image; do not invent anything else):",
}
STORY = {"it": "Giornata «{title}»: {text}", "en": "Day «{title}»: {text}"}
TAKEN = {"it": "Foto scattata {when}.", "en": "Photo taken {when}."}
TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):\d{2})?")

PART_OF_DAY = {
    "it": ("di mattina", "nel primo pomeriggio", "nel tardo pomeriggio", "di sera", "di notte"),
    "en": ("in the morning", "in the early afternoon", "in the late afternoon", "in the evening", "at night"),
}


@dataclass(frozen=True)
class Story:
    title: str
    text: str
    ids: frozenset[int]


def _part_of_day(hour: int) -> int:
    if 5 <= hour < 12:
        return 0
    if 12 <= hour < 16:
        return 1
    if 16 <= hour < 18:
        return 2
    if 18 <= hour < 22:
        return 3
    return 4


def describe_moment(taken_at_local: str | None, language: str = "it") -> str:
    """'il 14 giugno 2025, di sera' (o in inglese 'on 2025-06-14, in the evening'); vuoto se la data non c'è."""
    match = TIME_RE.match(taken_at_local or "")
    if not match:
        return ""
    year, month, day = int(match[1]), int(match[2]), int(match[3])
    if not 1 <= month <= 12:
        return ""
    lang = language if language in PART_OF_DAY else "it"
    part = PART_OF_DAY[lang][_part_of_day(int(match[4]))] if match[4] is not None else ""
    date = f"il {day} {MONTHS[month - 1]} {year}" if lang == "it" else f"on {year:04d}-{month:02d}-{day:02d}"
    return f"{date}, {part}" if part else date


def compose(about: str, stories: Sequence[tuple[str, str]], when: str, language: str = "it") -> str:
    """Testo di contesto per il prompt; vuoto se non c'è niente da dire."""
    lang = language if language in HEADER else "it"
    parts: list[str] = []
    if about.strip():
        parts.append(about.strip()[:MAX_ABOUT])
    for title, text in stories[:MAX_STORIES]:
        parts.append(STORY[lang].format(title=title, text=text.strip()[:MAX_STORY]))
    if when:
        parts.append(TAKEN[lang].format(when=when))
    return f"{HEADER[lang]} " + " ".join(parts) if parts else ""


def load_stories(ctx: Context) -> list[Story]:
    """Album con una descrizione scritta dall'utente, con le foto che contengono."""
    conn = ctx.db.conn()
    out: list[Story] = []
    for album in conn.execute(
            "SELECT uid, title, description FROM albums WHERE deleted_at IS NULL AND trim(description)!=''").fetchall():
        where, params = build_where(conn, parse(f"album:{album['uid']}"), default_visibility=False)
        ids = frozenset(r[0] for r in conn.execute(f"SELECT p.id FROM photos p WHERE {where}", params))
        if ids:
            out.append(Story(album["title"], album["description"], ids))
    return out


def stories_for(stories: Sequence[Story], photo_id: int) -> list[tuple[str, str]]:
    """Le storie che riguardano la foto, dalla più specifica (album più piccolo) alla più generale."""
    found = sorted((s for s in stories if photo_id in s.ids), key=lambda s: len(s.ids))
    return [(s.title, s.text) for s in found]
