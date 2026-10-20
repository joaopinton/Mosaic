"""Testi del poster (D-26): i riquadri colorati portano i dati dell'evento, in negativo (chiaro su scuro).

Occasione, data, luogo, orario, persone, numero di foto: il riquadro più grande prende l'occasione. Il testo
è impaginato una volta sola, in pixel del file finale, e l'anteprima riceve le stesse righe e le stesse misure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mosaic.context import Context

# riquadro → (fondo, testo). In negativo: testo chiaro su rosso e blu; sul giallo resta l'inchiostro scuro.
# Niente riquadri neri: sulle righe nere di Mondrian sembravano buchi.
TEXT_BLOCKS = (("#d52b1e", "#fbf8f1"), ("#1e4b9b", "#fbf8f1"), ("#f7d117", "#141414"))
TRIM = 0.04           # per data e orario si scarta il 4% di foto agli estremi: una foto del mattino dopo non allunga la serata
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
          "novembre", "dicembre")
FONT_FILES = ("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf")
PAD = 0.09            # margine interno: frazione del lato corto del riquadro
LABEL_SIZE = 0.085    # etichetta: frazione del lato corto (con un minimo leggibile)
MAX_LINES = 4


@dataclass(frozen=True)
class Fact:
    label: str
    value: str


@dataclass(frozen=True)
class TextFit:
    lines: tuple[str, ...]
    value_px: int
    label_px: int
    pad_px: int


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _day(d: datetime, year: bool = True) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}" + (f" {d.year}" if year else "")


def format_dates(first: datetime, last: datetime) -> str:
    """«13 settembre 2025», «13–14 settembre 2025», «30 settembre – 2 ottobre 2025», anni diversi per intero."""
    if first.date() == last.date():
        return _day(first)
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{last.day} {MONTHS[first.month - 1]} {first.year}"
    if first.year == last.year:
        return f"{_day(first, year=False)} – {_day(last)}"
    return f"{_day(first)} – {_day(last)}"


def event_facts(ctx: Context, photo_ids: list[int], occasion: str = "", date: str = "", place: str = "") -> list[Fact]:
    """Dati dell'evento, nell'ordine in cui riempiono i riquadri (dal più grande). Quelli vuoti si saltano."""
    if not photo_ids:
        return [Fact("", occasion.strip())] if occasion.strip() else []
    marks = ",".join("?" * len(photo_ids))
    times = _trimmed(sorted(t for t in (_when(r[0]) for r in ctx.db.conn().execute(
        f"SELECT taken_at_local FROM photos WHERE id IN ({marks})", photo_ids)) if t))
    people = ctx.db.scalar(
        f"SELECT COUNT(DISTINCT COALESCE('s'||subj_id, 'f'||face_id)) FROM markers WHERE photo_id IN ({marks}) "
        f"AND invalid=0 AND type='face' AND (subj_id IS NOT NULL OR face_id IS NOT NULL)", tuple(photo_ids)) or 0
    facts = [Fact("", occasion.strip())] if occasion.strip() else []
    when = date.strip() or (format_dates(times[0], times[-1]) if times else "")
    if when:
        facts.append(Fact("Data", when))
    if place.strip():
        facts.append(Fact("Luogo", place.strip()))
    span = f"{times[0]:%H:%M}–{times[-1]:%H:%M}" if times else ""
    if times and (times[-1] - times[0]).total_seconds() < 36 * 3600 and f"{times[0]:%H:%M}" != f"{times[-1]:%H:%M}":
        facts.append(Fact("Orario", span.replace("–", " – ")))  # con gli spazi il valore va a capo nei riquadri stretti
    if people:
        facts.append(Fact("Persone", str(people)))
    facts.append(Fact("Foto", str(len(photo_ids))))
    return facts


def _trimmed(times: list[datetime]) -> list[datetime]:
    """Senza le foto isolate agli estremi (dal 10 foto in su): una foto scattata la mattina dopo non conta."""
    if len(times) < 10:
        return times
    cut = max(1, round(len(times) * TRIM))
    return times[cut:-cut]


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    windir = os.environ.get("WINDIR")
    if not windir:
        return None
    fonts = Path(windir) / "Fonts"
    for name in FONT_FILES:
        if (fonts / name).is_file():
            return str(fonts / name)
    return None


@lru_cache(maxsize=256)
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, size: int, width: int) -> list[str] | None:
    """Righe che stanno in `width` pixel alla misura `size`, oppure None se una parola sola è troppo larga."""
    f = font(size)
    lines: list[str] = []
    for word in text.split():
        if draw.textlength(word, font=f) > width:
            return None
        if lines and draw.textlength(f"{lines[-1]} {word}", font=f) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def fit_text(fact: Fact, width: int, height: int) -> TextFit:
    """La misura più grande del valore che sta nel riquadro (con l'etichetta sopra), con le righe già spezzate."""
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    short = min(width, height)
    pad = max(2, round(PAD * short))
    label_px = max(8, round(LABEL_SIZE * short)) if fact.label else 0
    inner_w, inner_h = width - 2 * pad, height - 2 * pad - (round(label_px * 1.6) if label_px else 0)
    best = TextFit((fact.value,), 6, label_px, pad)
    low, high = 6, max(6, inner_h)
    while low <= high:
        size = (low + high) // 2
        lines = _wrap(draw, fact.value, size, inner_w)
        if lines is not None and len(lines) <= MAX_LINES and len(lines) * size * 1.08 <= inner_h:
            best = TextFit(tuple(lines), size, label_px, pad)
            low = size + 1
        else:
            high = size - 1
    return best


def draw_fact(canvas: Image.Image, box: tuple[int, int, int, int], fg: str, fact: Fact, fit: TextFit) -> None:
    """Etichetta in alto a sinistra, valore in basso a sinistra: impaginazione svizzera, testo in negativo."""
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas)
    if fact.label:
        draw.text((x0 + fit.pad_px, y0 + fit.pad_px), fact.label.upper(), font=font(fit.label_px), fill=fg)
    size = fit.value_px
    top = y1 - fit.pad_px - round(len(fit.lines) * size * 1.08)
    for i, line in enumerate(fit.lines):
        draw.text((x0 + fit.pad_px, top + round(i * size * 1.08)), line, font=font(size), fill=fg)
