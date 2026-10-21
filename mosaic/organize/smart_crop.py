"""Ritaglio intelligente a un formato prestabilito, per le foto esportate (D-34).

Il riquadro è il più grande con le proporzioni richieste che sta nella foto, quindi si sposta lungo un solo asse.
La posizione tiene dentro i volti (marker di D-22) e non ne taglia nessuno a metà: se non entrano tutti, restano
interi i più grandi. Senza volti segue il soggetto principale (colore e dettaglio sulla miniatura), con il
centro poco sopra la metà come il poster (D-26). Gli originali non si toccano: si ritagliano solo le copie.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

Face = tuple[float, float, float, float]  # x, y, w, h in frazioni (come i marker)
Box = tuple[int, int, int, int]           # x0, y0, x1, y1 in pixel
Span = tuple[float, float]                # intervallo lungo un asse, in frazioni

MAX_SIDE = 20000
MAX_RATIO = 10.0
EXACT_LOSS = 0.01       # si perde fino all'1% del lato pur di avere il rapporto esatto (multiplo intero di w:h)
STEPS = 401             # posizioni provate lungo l'asse libero
SALIENCY_SIDE = 64      # lato lungo della mappa di salienza
CENTER = (0.5, 0.45)    # fuoco senza volti: poco sopra la metà (D-26)
PRIOR_SIGMA = 0.3
PRIOR_FLOOR = 0.05      # peso del solo centro, sotto agli indizi
COLOR_FLOOR = 20.0      # differenze di colore (Lab) e di dettaglio sotto queste soglie contano poco
DETAIL_FLOOR = 60.0
HEADROOM = 0.4          # tagliando sopra e sotto, i volti stanno al 40% dell'altezza del riquadro
# pesi del costo di una posizione
CUT_FACE = 6.0          # volto (il box del marker) tagliato, anche di poco
MISS_FACE = 3.0         # parte di volto fuori dal riquadro
CUT_HEAD = 1.5          # capelli, orecchie o mento tagliati a metà
CUT_BODY = 1.0          # spalle o busto tagliati a metà
SALIENT = 2.0           # soggetto principale lasciato fuori
OFF_CENTER = 0.2        # a parità del resto, riquadro centrato sul fuoco


@dataclass(frozen=True)
class Format:
    w: int
    h: int
    pixels: bool = False  # True: le copie escono esattamente di w×h pixel; False: w:h è solo un rapporto

    def __post_init__(self) -> None:
        if not (1 <= self.w <= MAX_SIDE and 1 <= self.h <= MAX_SIDE):
            raise ValueError(f"formato non valido: {self.w}×{self.h}")
        if max(self.w / self.h, self.h / self.w) > MAX_RATIO:
            raise ValueError("proporzioni troppo estreme: al massimo 10:1")

    @property
    def ratio(self) -> float:
        return self.w / self.h


def crop_size(width: int, height: int, fmt: Format) -> tuple[int, int]:
    """Il riquadro più grande con le proporzioni del formato; esatte se costa poco, altrimenti a meno di un pixel."""
    g = math.gcd(fmt.w, fmt.h)
    p, q = fmt.w // g, fmt.h // g
    full_w, full_h = (height * p / q, float(height)) if width * q >= height * p else (float(width), width * q / p)
    k = min(width // p, height // q)
    if k >= 1 and k * p >= full_w * (1 - EXACT_LOSS):
        return k * p, k * q
    return max(1, min(width, round(full_w))), max(1, min(height, round(full_h)))


# --- dove sta il soggetto ------------------------------------------------------------------------------

def saliency(img: Image.Image) -> np.ndarray:
    """Mappa del soggetto principale su una miniatura, pesata verso il centro; somma 1.

    Somma di due indizi: colore lontano dalla media della foto (Achanta 2009) e dettaglio, cioè le parti a fuoco.
    Ognuno conta in proporzione a quanto è marcato: su una foto piatta resta solo il centro poco sopra la metà.
    """
    small = img.convert("RGB")
    small.thumbnail((SALIENCY_SIDE, SALIENCY_SIDE), Image.BILINEAR, reducing_gap=2.0)
    lab = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2LAB).astype(np.float32)
    color = np.linalg.norm(cv2.GaussianBlur(lab, (0, 0), 1.0) - lab.reshape(-1, 3).mean(axis=0), axis=2)
    gray = lab[..., 0]
    detail = cv2.GaussianBlur(np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)), (0, 0), 2.0)
    sal = color / max(float(color.max()), COLOR_FLOOR) + detail / max(float(detail.max()), DETAIL_FLOOR)
    h, w = sal.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    prior = np.exp(-(((xx + 0.5) / w - CENTER[0]) ** 2 + ((yy + 0.5) / h - CENTER[1]) ** 2) / (2 * PRIOR_SIGMA**2))
    sal = (sal + PRIOR_FLOOR) * prior
    return sal / float(sal.sum())


def _subjects(faces: Sequence[Face], axis: int) -> list[tuple[Span, Span, Span, float]]:
    """Per ogni volto, lungo l'asse: (volto, testa con capelli e mento, spalle o busto, peso).

    Solo il volto è un vincolo duro; testa e busto costano meno, così due volti che entrano appena restano interi.
    I volti grandi pesano di più. Tutto è limitato alla foto: un volto già tagliato dal bordo conta per quel che si vede.
    """
    biggest = max(w * h for _, _, w, h in faces) or 1.0
    out = []
    for x, y, w, h in faces:
        if axis == 0:
            face, head, body = (x, x + w), (x - 0.1 * w, x + 1.1 * w), (x + w / 2 - 1.5 * w, x + w / 2 + 1.5 * w)
        else:
            face, head, body = (y, y + h), (y - 0.3 * h, y + 1.1 * h), (y - 0.3 * h, y + 2.5 * h)
        out.append((_clip(*face), _clip(*head), _clip(*body), math.sqrt(w * h / biggest)))
    return out


def _clip(a: float, b: float) -> Span:
    return max(a, 0.0), min(b, 1.0)


def _focus(faces: Sequence[Face], axis: int) -> float:
    """Centro dei volti pesato per grandezza, come il poster."""
    weights = [w * h for _, _, w, h in faces]
    total = sum(weights) or 1.0
    return sum((f[axis] + f[axis + 2] / 2) * wt for f, wt in zip(faces, weights, strict=True)) / total


def _visible(starts: np.ndarray, span: float, a: float, b: float) -> np.ndarray:
    """Quota dell'intervallo [a, b] che entra in ogni riquadro [s, s + span]."""
    if b <= a:
        return np.ones_like(starts)
    return np.clip(np.minimum(starts + span, b) - np.maximum(starts, a), 0.0, None) / (b - a)


def _halved(seen: np.ndarray) -> np.ndarray:
    """0 se la parte è tutta dentro o tutta fuori, 1 se è tagliata a metà."""
    return 1 - np.abs(2 * seen - 1)


def _mass(profile: np.ndarray, starts: np.ndarray, span: float) -> np.ndarray:
    """Quota della salienza (già proiettata sull'asse) dentro ogni riquadro."""
    cum = np.concatenate([[0.0], np.cumsum(profile, dtype=np.float64)])
    cum /= cum[-1] or 1.0
    grid = np.linspace(0.0, 1.0, len(cum))
    return np.interp(starts + span, grid, cum) - np.interp(starts, grid, cum)


def _best_start(span: float, axis: int, faces: Sequence[Face], sal: np.ndarray | None) -> float:
    """Inizio del riquadro (frazione del lato) con il costo più basso."""
    starts = np.linspace(0.0, 1.0 - span, STEPS)
    cost = np.zeros(STEPS)
    if faces:
        for face, head, body, weight in _subjects(faces, axis):
            seen = _visible(starts, span, *face)
            cut = (seen > 1e-3) & (seen < 1 - 1e-3)
            cost += weight * (CUT_FACE * cut + MISS_FACE * (1 - seen))
            cost += weight * CUT_HEAD * _halved(_visible(starts, span, *head))
            cost += weight * CUT_BODY * _halved(_visible(starts, span, *body))
        focus = _focus(faces, axis) + (span * (0.5 - HEADROOM) if axis == 1 else 0.0)
    else:
        focus = CENTER[axis]
        if sal is not None:
            cost += SALIENT * (1 - _mass(sal.sum(axis=axis), starts, span))
    cost += OFF_CENTER * np.abs(starts + span / 2 - focus)
    return float(starts[int(np.argmin(cost))])


def crop_box(width: int, height: int, fmt: Format, faces: Sequence[Face] = (), sal: np.ndarray | None = None) -> Box:
    """Il ritaglio in pixel della foto `width`×`height` con le proporzioni di `fmt`, messo sui soggetti."""
    cw, ch = crop_size(width, height, fmt)
    offsets = []
    for axis, length, size in ((0, width, cw), (1, height, ch)):
        if size >= length:
            offsets.append(0)
            continue
        start = _best_start(size / length, axis, faces, sal)
        offsets.append(min(max(round(start * length), 0), length - size))
    x0, y0 = offsets
    return x0, y0, x0 + cw, y0 + ch


def apply(img: Image.Image, box: Box, fmt: Format) -> Image.Image:
    """Ritaglia e, per i formati in pixel, porta alla misura esatta."""
    out = img.crop(box)
    if fmt.pixels and out.size != (fmt.w, fmt.h):
        out = out.resize((fmt.w, fmt.h), Image.LANCZOS, reducing_gap=3.0)
    return out
