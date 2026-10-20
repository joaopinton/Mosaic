"""Generazione delle miniature: l'originale si decodifica una volta e le taglie derivano dalla più grande già pronta."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageOps

from mosaic.context import Context
from mosaic.thumbs.sizes import SIZES, Size, get

Image.MAX_IMAGE_PIXELS = 400_000_000  # protezione dalle decompression bomb, senza bloccare i panorami

try:  # HEIC/AVIF tramite pillow-heif, se installato
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass


def thumb_path(thumbs_dir: Path, sha1: str, size: Size) -> Path:
    """cache/thumbs/<h0>/<h1>/<h2>/<sha1>_<W>x<H>_<metodo>.jpg"""
    return thumbs_dir / sha1[0] / sha1[1] / sha1[2] / f"{sha1}_{size.w}x{size.h}_{size.method}.jpg"


def open_image(path: Path, target: int | None = None) -> Image.Image:
    """Apre un'immagine, applica l'orientamento EXIF e la porta in RGB (alpha su fondo bianco)."""
    img = Image.open(path)
    if target and img.format == "JPEG":
        img.draft("RGB", (target, target))  # decodifica ridotta: molto più veloce sui JPEG grandi
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    if img.mode == "I;16":
        img = img.point(lambda v: v / 256).convert("L")
    return img.convert("RGB")


def _make(source: Image.Image, size: Size) -> Image.Image:
    """Crea una taglia senza ingrandire mai l'immagine."""
    if size.crop:
        side = min(source.width, source.height)
        left, top = (source.width - side) // 2, (source.height - side) // 2
        square = source.crop((left, top, left + side, top + side))
        target = min(size.w, side)
        return square if target == side else square.resize((target, target), Image.LANCZOS)
    result = source.copy()
    result.thumbnail((size.w, size.h), Image.LANCZOS)
    return result


def _save(img: Image.Image, dest: Path, quality: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    img.save(tmp, "JPEG", quality=quality, optimize=True, progressive=False)
    os.replace(tmp, dest)


def generate_set(ctx: Context, sha1: str, source: Path, names: tuple[str, ...] | None = None) -> dict[str, Path]:
    """Genera le taglie mancanti. Ritorna nome → percorso per tutte quelle richieste."""
    wanted = [get(n) for n in (names or ctx.cfg.thumbs.pregenerate)]
    paths = {s.name: thumb_path(ctx.thumbs_dir, sha1, s) for s in wanted}
    missing = [s for s in wanted if not paths[s.name].exists()]
    if not missing:
        return paths
    missing.sort(key=lambda s: (-s.longest, s.crop))
    quality = ctx.cfg.thumbs.quality
    base = open_image(source, target=max(s.longest for s in missing))
    pool: list[Image.Image] = [base]
    for size in missing:
        # sorgente più piccola che sia ancora abbastanza grande per questa taglia
        usable = [img for img in pool if (min(img.size) >= size.w if size.crop else max(img.size) >= size.longest)]
        origin = min(usable, key=lambda im: im.width * im.height) if usable else pool[0]
        made = _make(origin, size)
        _save(made, paths[size.name], quality)
        if not size.crop:
            pool.append(made)
    return paths


def ensure_thumb(ctx: Context, sha1: str, source: Path, name: str) -> Path:
    """Una sola taglia, creata su richiesta (le taglie grandi non sono pre-generate)."""
    return generate_set(ctx, sha1, source, (name,))[name]


def valid_size(name: str) -> bool:
    return name in SIZES
