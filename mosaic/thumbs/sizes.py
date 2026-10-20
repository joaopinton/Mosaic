"""Catalogo delle taglie delle miniature (stessa idea di internal/thumb/sizes.go di PhotoPrism)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Size:
    name: str
    w: int
    h: int
    crop: bool  # True = tile (ritaglio centrale), False = fit (stessa proporzione, dentro il riquadro)

    @property
    def method(self) -> str:
        return "tile" if self.crop else "fit"

    @property
    def longest(self) -> int:
        return max(self.w, self.h)


def _tile(n: int) -> Size:
    return Size(f"tile_{n}", n, n, True)


SIZES: dict[str, Size] = {
    s.name: s
    for s in (
        _tile(50), _tile(100), _tile(224), _tile(500), _tile(1080),
        Size("fit_720", 720, 720, False),
        Size("fit_1280", 1280, 1024, False),
        Size("fit_1920", 1920, 1200, False),
        Size("fit_2048", 2048, 2048, False),
        Size("fit_2560", 2560, 1600, False),
        Size("fit_3840", 3840, 3840, False),
        Size("fit_4096", 4096, 4096, False),
    )
}

DEFAULT_PREGENERATED = ("tile_50", "tile_100", "tile_224", "tile_500", "fit_720", "fit_1280", "fit_1920")


def get(name: str) -> Size:
    try:
        return SIZES[name]
    except KeyError:
        raise KeyError(f"taglia sconosciuta: {name}") from None
