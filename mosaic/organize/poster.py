"""Poster a mosaico stile Mondrian dalle foto di una selezione (D-26).

Impaginazione: il foglio si divide ricorsivamente in due (tagli verticali e orizzontali, come nei quadri di Mondrian)
finché ogni foto ha il suo riquadro. Si provano molte suddivisioni casuali (con un seme, quindi ripetibili) e si
tiene quella che taglia meno le foto: ogni riquadro somiglia alle proporzioni della sua foto, le foto migliori
prendono i riquadri più grandi, nessun riquadro è una striscia sottile. Nello stile Mondrian si aggiungono alcuni
riquadri pieni rosso, giallo, blu e bianco, separati da righe nere spesse.

Il ritaglio di ogni foto si centra sui volti (marker) quando ci sono. L'anteprima nell'interfaccia usa la stessa
impaginazione e gli stessi ritagli del file finale.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from mosaic.context import Context
from mosaic.organize.poster_text import TEXT_BLOCKS, Fact, TextFit, draw_fact, event_facts, fit_text
from mosaic.pipeline import thumb_source
from mosaic.thumbs.render import open_image

log = logging.getLogger(__name__)

# carta in millimetri (lato corto, lato lungo)
PAPERS: dict[str, tuple[float, float]] = {
    "a4": (210, 297), "a3": (297, 420), "a2": (420, 594), "30x40": (300, 400), "50x70": (500, 700),
    "70x100": (700, 1000), "square": (500, 500),
}
STYLES = ("mondrian", "gallery", "none")
LINES_MM = {"thin": 0.006, "medium": 0.012, "bold": 0.02}  # spessore come frazione del lato corto
MONDRIAN = tuple(bg for bg, _ in TEXT_BLOCKS)               # rosso, blu, nero, giallo: i riquadri con i dati
TEXT = "text"                                               # segnaposto: il colore si sceglie dopo l'impaginazione
INK = {"mondrian": "#141414", "gallery": "#ffffff", "none": "#141414"}
MAX_PIXELS = 30_000_000     # oltre si abbassano i dpi: un poster da 70×100 a 200 dpi non sta comodo in 8 GB
MIN_SIDE = 0.07             # lato minimo di un riquadro, come frazione del lato corto del foglio
SIZES = ("fit_1280", "fit_1920", "fit_2560", "fit_4096")
NAME_RE = re.compile(r"^[\w.-]{1,120}\.jpg$")


class PosterError(ValueError):
    """Opzioni non valide o foto mancanti."""


@dataclass(frozen=True)
class Options:
    paper: str = "a3"
    landscape: bool = False
    style: str = "mondrian"
    line: str = "medium"
    colors: int = 3          # riquadri colorati (solo Mondrian)
    seed: int = 1
    dpi: int = 200
    occasion: str = ""     # dati scritti nei riquadri colorati (vuoti = calcolati o saltati)
    date: str = ""
    place: str = ""

    def __post_init__(self) -> None:
        if self.paper not in PAPERS:
            raise PosterError(f"formato sconosciuto: {self.paper}")
        if self.style not in STYLES:
            raise PosterError(f"stile sconosciuto: {self.style}")
        if self.line not in LINES_MM:
            raise PosterError(f"spessore sconosciuto: {self.line}")
        if not 0 <= self.colors <= 8 or not 72 <= self.dpi <= 300:
            raise PosterError("opzioni del poster fuori dai limiti")
        if max(len(self.occasion), len(self.date), len(self.place)) > 80:
            raise PosterError("testi del poster troppo lunghi (al massimo 80 caratteri)")

    @property
    def size_mm(self) -> tuple[float, float]:
        short, long = PAPERS[self.paper]
        return (long, short) if self.landscape else (short, long)


@dataclass
class Piece:
    """Una foto (id) o un riquadro pieno (color), con le proporzioni desiderate e un peso (area relativa)."""
    weight: float
    aspect: float | None = None   # larghezza / altezza; None per i riquadri pieni (vanno bene di ogni forma)
    photo_id: int | None = None
    color: str | None = None
    focus: tuple[float, float] = (0.5, 0.45)


@dataclass
class Cell:
    x: float
    y: float
    w: float
    h: float
    piece: Piece
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # ritaglio normalizzato sulla foto
    fill: str = ""                  # riquadro con i dati: fondo, testo, dato e misure del testo
    fg: str = ""
    fact: Fact | None = None
    fit: TextFit | None = None


@dataclass
class Layout:
    width: float
    height: float
    cells: list[Cell] = field(default_factory=list)
    cost: float = 0.0


# --- impaginazione ------------------------------------------------------------------------------------

ASPECT_SHARE = 0.7   # i tagli seguono per il 70% le proporzioni delle foto e per il 30% la loro importanza
RESTARTS, STEPS = 24, 120
BUDGET = 150_000      # riquadri × valutazioni: circa un secondo, anche con 200 foto


def _tree(indices: list[int], rng: np.random.Generator) -> list:
    """Albero binario casuale: ["L", indice] oppure ["V" | "H", sinistra, destra] (affiancati o impilati)."""
    if len(indices) == 1:
        return ["L", indices[0]]
    k = int(rng.integers(1, len(indices)))
    return ["V" if rng.random() < 0.5 else "H", _tree(indices[:k], rng), _tree(indices[k:], rng)]


def _measure_all(node: list, aspects: list[float], weights: list[float], out: dict[int, tuple[float, float]]) -> tuple[float, float]:
    """(proporzioni naturali, peso) di ogni sottoalbero, in un solo passaggio dal basso: affiancati si sommano le
    larghezze, impilati le altezze. Il risultato va in `out`, indicizzato per id del nodo."""
    if node[0] == "L":
        result = (aspects[node[1]], weights[node[1]])
    else:
        (a, wa), (b, wb) = _measure_all(node[1], aspects, weights, out), _measure_all(node[2], aspects, weights, out)
        result = ((a + b if node[0] == "V" else 1 / (1 / a + 1 / b)), wa + wb)
    out[id(node)] = result
    return result


def _realize(node: list, measures: dict[int, tuple[float, float]], pieces: list[Piece],
             x: float, y: float, w: float, h: float, out: list[Cell]) -> None:
    if node[0] == "L":
        out.append(Cell(x, y, w, h, pieces[node[1]]))
        return
    (a, wa), (b, wb) = measures[id(node[1])], measures[id(node[2])]
    by_weight = wa / (wa + wb)
    if node[0] == "V":
        f = ASPECT_SHARE * a / (a + b) + (1 - ASPECT_SHARE) * by_weight
        _realize(node[1], measures, pieces, x, y, w * f, h, out)
        _realize(node[2], measures, pieces, x + w * f, y, w * (1 - f), h, out)
    else:
        f = ASPECT_SHARE * (1 / a) / (1 / a + 1 / b) + (1 - ASPECT_SHARE) * by_weight
        _realize(node[1], measures, pieces, x, y, w, h * f, out)
        _realize(node[2], measures, pieces, x, y + h * f, w, h * (1 - f), out)


def _cost(cells: list[Cell], short: float, area: float) -> float:
    """Quanto si tagliano le foto, quanto le aree tradiscono l'importanza, strisce sottili."""
    total = 0.0
    for c in cells:
        share = c.w * c.h / area
        if min(c.w, c.h) < MIN_SIDE * short:
            total += 2.0 + (MIN_SIDE * short - min(c.w, c.h)) / short * 40
        if c.piece.aspect is None:
            total += 0.3 * max(0.0, abs(math.log(c.w / c.h)) - math.log(2.5))  # i colori: niente strisce
            continue
        total += abs(math.log((c.w / c.h) / c.piece.aspect)) * (1 + 6 * share)
        total += 0.35 * abs(math.log(share / c.piece.weight))
    return total


def _internal(node: list, out: list[list]) -> list[list]:
    if node[0] != "L":
        out.append(node)
        _internal(node[1], out)
        _internal(node[2], out)
    return out


def _leaves(node: list, out: list[list]) -> list[list]:
    if node[0] == "L":
        out.append(node)
    else:
        _leaves(node[1], out)
        _leaves(node[2], out)
    return out


def _flip(node: list) -> None:
    node[0] = "H" if node[0] == "V" else "V"


def _swap(a: list, b: list) -> None:
    a[1], b[1] = b[1], a[1]


def layout(pieces: list[Piece], width: float, height: float, seed: int, tries: int = RESTARTS) -> Layout:
    """Impaginazione ad albero del foglio `width`×`height`: ripartenze casuali, poi miglioramento locale
    (scambio di due riquadri, taglio girato) finché il costo scende."""
    if not pieces:
        raise PosterError("nessuna foto per il poster")
    rng = np.random.default_rng(seed)
    total_w = sum(p.weight for p in pieces)
    weights = [p.weight / total_w for p in pieces]
    pieces = [Piece(w, p.aspect, p.photo_id, p.color, p.focus) for p, w in zip(pieces, weights, strict=True)]
    short, area = min(width, height), width * height
    best: Layout | None = None

    def evaluate(tree: list, aspects: list[float]) -> tuple[float, list[Cell]]:
        measures: dict[int, tuple[float, float]] = {}
        _measure_all(tree, aspects, weights, measures)
        cells: list[Cell] = []
        _realize(tree, measures, pieces, 0.0, 0.0, width, height, cells)
        return _cost(cells, short, area), cells

    # lavoro proporzionato al numero di riquadri: stessa durata (e stesso risultato) per anteprima e file
    evaluations = BUDGET // len(pieces)
    steps = min(STEPS + 6 * len(pieces), evaluations // 2) if len(pieces) > 1 else 0
    restarts = max(2, min(tries, evaluations // max(steps, 1)))
    for _ in range(restarts):
        # i riquadri pieni non hanno proporzioni proprie: a ogni ripartenza una forma diversa assorbe le differenze
        aspects = [p.aspect if p.aspect is not None else float(math.exp(rng.uniform(-0.8, 0.8))) for p in pieces]
        tree = _tree([int(i) for i in rng.permutation(len(pieces))], rng)
        cost, cells = evaluate(tree, aspects)
        nodes, leaves = _internal(tree, []), _leaves(tree, [])
        for _ in range(steps):
            if nodes and rng.random() < 0.4:
                node = nodes[int(rng.integers(len(nodes)))]
                _flip(node)
                undo = (_flip, node)
            else:
                a, b = (leaves[int(i)] for i in rng.choice(len(leaves), 2, replace=False))
                _swap(a, b)
                undo = (_swap, a, b)
            new_cost, new_cells = evaluate(tree, aspects)
            if new_cost <= cost:
                cost, cells = new_cost, new_cells
            else:
                undo[0](*undo[1:])  # type: ignore[operator]
        if best is None or cost < best.cost:
            best = Layout(width, height, cells, cost)
    assert best is not None
    for c in best.cells:
        if c.piece.aspect is not None:
            c.crop = crop_box(c.piece.aspect, c.w / c.h, c.piece.focus)
    return best


def crop_box(photo_aspect: float, cell_aspect: float, focus: tuple[float, float]) -> tuple[float, float, float, float]:
    """Ritaglio (x0, y0, x1, y1) normalizzato della foto con le proporzioni del riquadro, centrato sul fuoco."""
    fx, fy = focus
    if photo_aspect > cell_aspect:   # foto più larga: si taglia ai lati
        span = cell_aspect / photo_aspect
        x0 = min(max(fx - span / 2, 0.0), 1.0 - span)
        return (x0, 0.0, x0 + span, 1.0)
    span = photo_aspect / cell_aspect  # foto più alta: si taglia sopra e sotto
    y0 = min(max(fy - span / 2, 0.0), 1.0 - span)
    return (0.0, y0, 1.0, y0 + span)


# --- foto e pezzi ------------------------------------------------------------------------------------

def _photo_rows(ctx: Context, uids: list[str]) -> list[dict]:
    marks = ",".join("?" * len(uids))
    rows = {r["uid"]: dict(r) for r in ctx.db.all(
        f"SELECT p.id, p.uid, p.width, p.height, p.favorite, a.sharpness FROM photos p "
        f"LEFT JOIN photo_aesthetics a ON a.photo_id=p.id WHERE p.uid IN ({marks}) AND p.deleted_at IS NULL", tuple(uids))}
    return [rows[u] for u in uids if u in rows and rows[u]["width"] and rows[u]["height"]]


def _focus(ctx: Context, photo_id: int) -> tuple[float, float]:
    """Centro dei volti (pesato per grandezza), un po' sopra il centro se non ce ne sono."""
    faces = ctx.db.all("SELECT x, y, w, h FROM markers WHERE photo_id=? AND invalid=0 AND type='face'", (photo_id,))
    if not faces:
        return (0.5, 0.45)
    weights = [f["w"] * f["h"] for f in faces]
    total = sum(weights) or 1.0
    return (sum((f["x"] + f["w"] / 2) * wt for f, wt in zip(faces, weights, strict=True)) / total,
            sum((f["y"] + f["h"] / 2) * wt for f, wt in zip(faces, weights, strict=True)) / total)


def pieces_for(ctx: Context, uids: list[str], opts: Options) -> list[Piece]:
    rows = _photo_rows(ctx, uids)
    if not rows:
        raise PosterError("nessuna foto utilizzabile per il poster")
    sharp = np.array([r["sharpness"] if r["sharpness"] is not None else np.nan for r in rows], dtype=np.float64)
    score = np.nan_to_num(sharp, nan=float(np.nanmean(sharp)) if not np.isnan(sharp).all() else 0.0)
    score = score + 2.0 * np.array([bool(r["favorite"]) for r in rows])
    heroes = set(np.argsort(-score)[: max(1, len(rows) // 8)].tolist())
    rng = np.random.default_rng(opts.seed)
    pieces = [Piece(weight=(3.0 if i in heroes else float(rng.uniform(0.9, 1.5))), aspect=r["width"] / r["height"],
                    photo_id=r["id"], focus=_focus(ctx, r["id"])) for i, r in enumerate(rows)]
    if opts.style == "mondrian" and opts.colors:
        blocks = min(opts.colors, len(facts_for(ctx, [r["id"] for r in rows], opts)))  # mai riquadri vuoti
        pieces += [Piece(weight=float(rng.uniform(0.35, 0.7)), color=TEXT) for _ in range(blocks)]
    return pieces


def facts_for(ctx: Context, photo_ids: list[int], opts: Options) -> list[Fact]:
    return event_facts(ctx, photo_ids, opts.occasion, opts.date, opts.place)


_PLANS: dict[tuple, Layout] = {}
_PLAN_LOCK = threading.Lock()


def plan(ctx: Context, uids: list[str], opts: Options) -> Layout:
    """Impaginazione completa, una alla volta e in cache: il file finale riusa quella dell'anteprima."""
    key = (str(ctx.data_dir), tuple(uids), opts)
    with _PLAN_LOCK:
        if key not in _PLANS:
            if len(_PLANS) >= 16:
                _PLANS.pop(next(iter(_PLANS)))
            _PLANS[key] = _plan(ctx, uids, opts)
        return _PLANS[key]


def _plan(ctx: Context, uids: list[str], opts: Options) -> Layout:
    """Riquadri, ritagli e, nei riquadri colorati, i dati dell'evento già misurati in pixel."""
    width, height = opts.size_mm
    pieces = pieces_for(ctx, uids, opts)
    lay = layout(pieces, width, height, opts.seed)
    blocks = sorted((c for c in lay.cells if c.piece.color == TEXT), key=lambda c: -c.w * c.h)
    facts = facts_for(ctx, [p.photo_id for p in pieces if p.photo_id is not None], opts)
    px_w, px_h, _ = pixel_size(opts)
    line = line_px(opts, px_w, px_h)
    for i, (cell, fact) in enumerate(zip(blocks, facts)):  # il riquadro più grande prende l'occasione
        cell.fill, cell.fg = TEXT_BLOCKS[i % len(TEXT_BLOCKS)]
        cell.fact = fact
        x0, y0, x1, y1 = pixel_box(cell, lay, px_w / lay.width, line)
        cell.fit = fit_text(fact, x1 - x0, y1 - y0)
    return lay


def line_px(opts: Options, px_w: int, px_h: int) -> int:
    return 0 if opts.style == "none" else max(2, round(LINES_MM[opts.line] * min(px_w, px_h)))


def pixel_box(cell: Cell, lay: Layout, scale: float, line: int) -> tuple[int, int, int, int]:
    """Riquadro in pixel: mezza riga per lato tra i riquadri, una riga intera sul bordo del foglio."""
    half, rest = line // 2, line - line // 2
    x0 = round(cell.x * scale) + (line if cell.x == 0 else half)
    y0 = round(cell.y * scale) + (line if cell.y == 0 else half)
    x1 = round((cell.x + cell.w) * scale) - (line if abs(cell.x + cell.w - lay.width) < 1e-6 else rest)
    y1 = round((cell.y + cell.h) * scale) - (line if abs(cell.y + cell.h - lay.height) < 1e-6 else rest)
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


# --- disegno ----------------------------------------------------------------------------------------

def _source(ctx: Context, photo_id: int, need: int) -> Image.Image | None:
    """La miniatura più piccola che basta per `need` pixel di lato lungo (creata se manca), già orientata."""
    for name in SIZES:
        path = thumb_source(ctx, photo_id, name)
        if path is None:
            return None
        with Image.open(path) as probe:
            longest = max(probe.size)
        if longest >= need * 0.9 or name == SIZES[-1]:
            return open_image(path)
    return None


def pixel_size(opts: Options) -> tuple[int, int, int]:
    """Dimensioni in pixel e dpi effettivi (abbassati se il poster supera MAX_PIXELS)."""
    w_mm, h_mm = opts.size_mm
    dpi = opts.dpi
    while (w_mm / 25.4 * dpi) * (h_mm / 25.4 * dpi) > MAX_PIXELS and dpi > 72:
        dpi -= 10
    return round(w_mm / 25.4 * dpi), round(h_mm / 25.4 * dpi), dpi


def render(ctx: Context, uids: list[str], opts: Options, out: Path,
           progress: Callable[[int, int], None] | None = None) -> tuple[Path, int]:
    """Disegna il poster in `out` (JPEG). Ritorna (percorso, dpi effettivi)."""
    lay = plan(ctx, uids, opts)
    px_w, px_h, dpi = pixel_size(opts)
    scale = px_w / lay.width
    line = line_px(opts, px_w, px_h)
    canvas = Image.new("RGB", (px_w, px_h), INK[opts.style])
    for n, cell in enumerate(lay.cells, 1):
        x0, y0, x1, y1 = pixel_box(cell, lay, scale, line)
        size = (x1 - x0, y1 - y0)
        if cell.piece.color:
            canvas.paste(Image.new("RGB", size, cell.fill or INK[opts.style]), (x0, y0))
            if cell.fact and cell.fit:
                draw_fact(canvas, (x0, y0, x1, y1), cell.fg, cell.fact, cell.fit)
        else:
            img = _source(ctx, int(cell.piece.photo_id or 0), max(size))
            if img is not None:
                cx0, cy0, cx1, cy1 = cell.crop
                left, top = round(cx0 * img.width), round(cy0 * img.height)
                box = (left, top, max(left + 1, round(cx1 * img.width)), max(top + 1, round(cy1 * img.height)))
                canvas.paste(img.crop(box).resize(size, Image.LANCZOS), (x0, y0))
                img.close()
        if progress:
            progress(n, len(lay.cells))
    mark_storage(out.parent)
    tmp = out.with_name(out.name + ".part")
    canvas.save(tmp, "JPEG", quality=92, optimize=True, dpi=(dpi, dpi), subsampling=0)
    os.replace(tmp, out)
    return out, dpi


# --- dove si salvano ----------------------------------------------------------------------------------

def posters_dir() -> Path:
    """Immagini\\Mosaic dell'utente (o la cartella home se Immagini non c'è)."""
    pictures = Path.home() / "Pictures"
    return (pictures if pictures.is_dir() else Path.home()) / "Mosaic"


def mark_storage(folder: Path) -> None:
    """La cartella dei poster non si indicizza mai, anche se sta dentro la libreria (per esempio Immagini)."""
    from mosaic.index.discover import STORAGE_MARKER

    folder.mkdir(parents=True, exist_ok=True)
    (folder / STORAGE_MARKER).touch(exist_ok=True)


def poster_name(title: str, now: datetime | None = None) -> str:
    slug = re.sub(r"[^\w-]+", "-", title.lower()).strip("-")[:60] or "poster"
    return f"poster-{slug}-{(now or datetime.now()):%Y%m%d-%H%M%S}.jpg"


def poster_path(folder: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise PosterError("nome del poster non valido")
    return Path(folder) / name


def album_dir(ctx: Context, album) -> Path:  # type: ignore[no-untyped-def]
    """Dove vanno i poster di un album (D-27): per una cartella del disco la sua sottocartella «Mosaic» (con
    .mosaicstorage, quindi mai indicizzata); per gli altri album Immagini/Mosaic/<titolo>."""
    from mosaic.search.parser import parse
    from mosaic.textutil import slugify

    query = parse(album["filter"] or "")
    base: Path | None = None
    if (root := query.get("root")) is not None:
        base = Path(root.values[0])
    elif (folder := query.get("folder")) is not None:
        row = ctx.db.one("SELECT root FROM folders WHERE path=? LIMIT 1", (folder.values[0],))
        base = Path(row["root"]) / folder.values[0] if row else None
    if base is not None and base.is_dir() and os.access(base, os.W_OK):
        return base / "Mosaic"
    return posters_dir() / (slugify(album["title"]) or "album")


def list_posters(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("poster-*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
