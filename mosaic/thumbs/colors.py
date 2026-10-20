"""Colori dominanti con nomi italiani (evoluzione della mappa del vecchio Mosaic), luminanza e croma."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from PIL import Image

NERO, BIANCO, GRIGIO = "Nero", "Bianco", "Grigio"
MULTICOLORE = "Multicolore"


@dataclass(frozen=True)
class ColorInfo:
    main: str
    names: tuple[str, ...]
    luminance: float  # 0..1
    chroma: float     # 0..1 (saturazione media)


def color_name(r: int, g: int, b: int) -> str:
    """Nome italiano di un colore RGB, basato su tinta/saturazione/valore."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue = h * 360
    if v < 0.14:
        return NERO
    if s < 0.12:
        return BIANCO if v > 0.85 else GRIGIO
    if hue < 15 or hue >= 345:
        return "Marrone" if v < 0.45 else "Rosso"
    if hue < 45:
        return "Marrone" if v < 0.55 else "Arancione"
    if hue < 70:
        return "Giallo"
    if hue < 165:
        return "Verde"
    if hue < 200:
        return "Azzurro"
    if hue < 260:
        return "Blu"
    if hue < 300:
        return "Viola"
    return "Rosa"


def analyze(img: Image.Image, k: int = 3) -> ColorInfo:
    small = img.convert("RGB").resize((64, 64), Image.BOX)
    quantized = small.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)
    names: list[str] = []
    for _, idx in counts:
        name = color_name(*palette[idx * 3: idx * 3 + 3])
        if name not in names:
            names.append(name)
    gray = small.convert("L").tobytes()
    luminance = sum(gray) / (len(gray) * 255)
    raw = small.tobytes()
    sats = [colorsys.rgb_to_hsv(raw[i] / 255, raw[i + 1] / 255, raw[i + 2] / 255)[1] for i in range(0, len(raw), 3)]
    return ColorInfo(
        main=names[0] if names else MULTICOLORE,
        names=tuple(names) or (MULTICOLORE,),
        luminance=round(luminance, 4),
        chroma=round(sum(sats) / len(sats), 4),
    )
