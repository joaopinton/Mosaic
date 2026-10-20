"""Migliora luce e colori delle foto esportate (D-30): più luce sul soggetto, colori più vivi, tono coordinato.

Si misura ogni foto su una miniatura (veloce), poi si decide per tutto il gruppo insieme: così «tono coordinato» porta
le foto della selezione verso la stessa luce e lo stesso bilanciamento del bianco. Le correzioni si applicano in Lab
(OpenCV, uint8) a strisce, per tenere bassa la memoria anche con foto da 24 MP su 8 GB. Gli originali non si toccano.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import cv2
import numpy as np
from PIL import Image

STRENGTHS = {"light": 0.5, "medium": 0.8, "strong": 1.0}
SUBJECT_TARGET = 0.58        # luminosità (L di Lab, 0..1) a cui portare un soggetto troppo scuro
BRIGHT_SUBJECT = 0.74        # sopra questa il soggetto si abbassa un poco
GAMMA_MIN, GAMMA_MAX = 0.5, 1.4  # 0,5 ≈ +1 EV sui mezzitoni: oltre si vede il rumore
HARMONY = 0.6                # quota della distanza dalla mediana del gruppo che «tono coordinato» recupera
MAX_SHIFT = 12.0             # spostamento massimo di a*/b* (unità Lab uint8)
VIBRANCE = 0.35
STRIP = 512                  # righe per striscia


@dataclass(frozen=True)
class Options:
    light: bool = True        # più luce sul soggetto
    contrast: bool = True     # livelli automatici, prudenti
    vivid: bool = True        # colori più vivi (vibrance)
    harmonize: bool = True    # tono coordinato su tutta la selezione
    strength: str = "medium"

    def __post_init__(self) -> None:
        if self.strength not in STRENGTHS:
            raise ValueError(f"intensità non valida: {self.strength!r}")

    @property
    def k(self) -> float:
        return STRENGTHS[self.strength]

    @property
    def active(self) -> bool:
        return self.light or self.contrast or self.vivid or self.harmonize


@dataclass(frozen=True)
class Stats:
    subject: float   # luminosità media del soggetto (volti o centro), 0..1
    low: float       # 0,5° percentile della luminosità
    high: float      # 99,5° percentile
    chroma: float    # saturazione media, 0..1
    a: float         # media di a* sui mezzitoni (128 = neutro)
    b: float         # media di b* sui mezzitoni


@dataclass(frozen=True)
class Adjust:
    black: float = 0.0
    white: float = 1.0
    gamma: float = 1.0
    da: float = 0.0
    db: float = 0.0
    vibrance: float = 0.0

    @property
    def identity(self) -> bool:
        return (self.black, self.white, self.gamma, self.da, self.db, self.vibrance) == (0.0, 1.0, 1.0, 0.0, 0.0, 0.0)


Face = tuple[float, float, float, float]  # x, y, w, h in frazioni (come i marker)


def _subject_weights(h: int, w: int, faces: list[Face]) -> np.ndarray:
    """Pesi del soggetto: i volti (un po' allargati) se ci sono, altrimenti il centro, poco sopra la metà."""
    if faces:
        mask = np.zeros((h, w), np.float32)
        for x, y, fw, fh in faces:
            cx, cy, half_w, half_h = x + fw / 2, y + fh / 2, fw * 0.65, fh * 0.65
            x0, x1 = max(0, int((cx - half_w) * w)), min(w, math.ceil((cx + half_w) * w))
            y0, y1 = max(0, int((cy - half_h) * h)), min(h, math.ceil((cy + half_h) * h))
            mask[y0:y1, x0:x1] = 1.0
        if mask.sum() > 0:
            return mask
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.exp(-(((xx / w - 0.5) / 0.3) ** 2 + ((yy / h - 0.45) / 0.3) ** 2) / 2)


def measure(img: Image.Image, faces: list[Face] | None = None) -> Stats:
    """Misure su una miniatura RGB."""
    lab = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2LAB)
    lum = lab[..., 0].astype(np.float32) / 255.0
    a = lab[..., 1].astype(np.float32)
    b = lab[..., 2].astype(np.float32)
    weights = _subject_weights(*lum.shape, faces or [])
    chroma = np.hypot(a - 128, b - 128) / 128.0
    mid = (lum > 0.15) & (lum < 0.85)
    if mid.sum() < lum.size * 0.05:
        mid = np.ones_like(mid)
    low, high = np.percentile(lum, [0.5, 99.5])
    return Stats(subject=float((lum * weights).sum() / weights.sum()), low=float(low), high=float(high),
                 chroma=float(chroma.mean()), a=float(a[mid].mean()), b=float(b[mid].mean()))


def _levels(s: Stats, opts: Options) -> tuple[float, float]:
    if not opts.contrast:
        return 0.0, 1.0
    black = min(max(s.low, 0.0), 0.08) * opts.k
    white = 1.0 - min(max(1.0 - s.high, 0.0), 0.10) * opts.k
    return black, max(white, black + 0.5)


def _target(s: Stats, opts: Options) -> float:
    """Luminosità desiderata del soggetto, prima del tono coordinato."""
    if not opts.light:
        return s.subject
    if s.subject < SUBJECT_TARGET:
        return s.subject + opts.k * (SUBJECT_TARGET - s.subject)
    if s.subject > BRIGHT_SUBJECT:
        return s.subject + opts.k * 0.5 * (BRIGHT_SUBJECT - 0.06 - s.subject)
    return s.subject


def _gamma(subject: float, target: float, black: float, white: float) -> float:
    span = white - black
    before = min(max((subject - black) / span, 0.02), 0.98)
    after = min(max((target - black) / span, 0.02), 0.98)
    return min(max(math.log(after) / math.log(before), GAMMA_MIN), GAMMA_MAX)


def _limit(shift: float) -> float:
    return min(max(shift, -MAX_SHIFT), MAX_SHIFT)


def plan(stats: list[Stats], opts: Options) -> list[Adjust]:
    """Le correzioni di ogni foto, decise guardando tutto il gruppo."""
    if not stats or not opts.active:
        return [Adjust() for _ in stats]
    harmonize = opts.harmonize and len(stats) > 1
    targets = [_target(s, opts) for s in stats]
    if harmonize:
        center = float(np.median(targets))
        targets = [t + HARMONY * opts.k * (center - t) for t in targets]
    mid_a, mid_b = float(np.median([s.a for s in stats])), float(np.median([s.b for s in stats]))
    pull = HARMONY * opts.k
    out: list[Adjust] = []
    for s, target in zip(stats, targets, strict=True):
        black, white = _levels(s, opts)
        adjust = Adjust(black=black, white=white, gamma=_gamma(s.subject, target, black, white))
        if harmonize:
            adjust = replace(adjust, da=_limit(pull * (mid_a - s.a)), db=_limit(pull * (mid_b - s.b)))
        if opts.vivid:  # meno spinta dove i colori sono già carichi
            adjust = replace(adjust, vibrance=VIBRANCE * opts.k * min(max(1.2 - s.chroma / 0.3, 0.3), 1.0))
        out.append(adjust)
    return out


def _lut(adjust: Adjust) -> np.ndarray:
    x = np.arange(256, dtype=np.float32) / 255.0
    y = np.clip((x - adjust.black) / (adjust.white - adjust.black), 0.0, 1.0) ** adjust.gamma
    return np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)


def _color(lab: np.ndarray, adjust: Adjust) -> None:
    """Sposta a*/b* e dà vibrance: più spinta ai colori spenti, meno alla pelle."""
    a = lab[..., 1].astype(np.float32) - 128 + adjust.da
    b = lab[..., 2].astype(np.float32) - 128 + adjust.db
    if adjust.vibrance:
        chroma = np.hypot(a, b) / 128.0
        boost = adjust.vibrance * np.clip(1.0 - chroma / 0.6, 0.0, 1.0)
        hue = np.degrees(np.arctan2(b, a))
        skin = (a > 0) & (hue > 25) & (hue < 75)
        boost[skin] *= 0.4
        a *= 1.0 + boost
        b *= 1.0 + boost
    lab[..., 1] = np.clip(np.round(a + 128), 0, 255)
    lab[..., 2] = np.clip(np.round(b + 128), 0, 255)


def apply(img: Image.Image, adjust: Adjust) -> Image.Image:
    """Applica le correzioni a un'immagine RGB, a strisce di STRIP righe."""
    rgb = np.array(img.convert("RGB"))
    if adjust.identity:
        return Image.fromarray(rgb)
    lut = _lut(adjust)
    touch_color = adjust.da or adjust.db or adjust.vibrance
    for top in range(0, rgb.shape[0], STRIP):
        strip = rgb[top:top + STRIP]
        lab = cv2.cvtColor(strip, cv2.COLOR_RGB2LAB)
        lab[..., 0] = cv2.LUT(lab[..., 0], lut)
        if touch_color:
            _color(lab, adjust)
        rgb[top:top + STRIP] = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return Image.fromarray(rgb)
