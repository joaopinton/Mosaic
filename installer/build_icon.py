"""Genera mosaic.svg, la favicon del frontend e mosaic.ico (16-256 px) da un'unica geometria del logo.

Uso: python installer/build_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# Geometria su una griglia di 256 unità.
UNIT = 256
CORNER = 60
BORDER = 14
NAVY = "#0f1523"
RIM = "#ff334b"
DOT_R = 38
DOT_CENTERS = (86, 170)
DOTS = ("#ff334b", "#ff7a1a", "#00d2ff", "#ffc828")  # alto-sx, alto-dx, basso-sx, basso-dx

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SUPERSAMPLE = 8


def dot_positions():
    return [(x, y) for y in DOT_CENTERS for x in DOT_CENTERS]


def build_svg() -> str:
    inner = UNIT - 2 * BORDER
    dots = "".join(
        f'<circle cx="{x}" cy="{y}" r="{DOT_R}" fill="{color}"/>'
        for (x, y), color in zip(dot_positions(), DOTS)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {UNIT} {UNIT}">'
        f'<rect width="{UNIT}" height="{UNIT}" rx="{CORNER}" fill="{RIM}"/>'
        f'<rect x="{BORDER}" y="{BORDER}" width="{inner}" height="{inner}" rx="{CORNER - BORDER}" fill="{NAVY}"/>'
        f"{dots}</svg>\n"
    )


def pixel_layout(size: int) -> tuple[int, int, list[int]]:
    """Bordo, diametro dei pallini e loro origini in pixel interi, simmetrici: a 16 px niente sbavature."""
    k = size / UNIT
    border = max(1, round(BORDER * k))
    diameter = round(2 * DOT_R * k)
    gap = max(1, round((DOT_CENTERS[1] - DOT_CENTERS[0] - 2 * DOT_R) * k))
    if (size - 2 * diameter - gap) % 2:
        gap += 1
    start = (size - 2 * diameter - gap) // 2
    return border, diameter, [start, start + diameter + gap]


def render(size: int) -> Image.Image:
    ss = SUPERSAMPLE
    canvas = size * ss
    border, diameter, origins = pixel_layout(size)
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = CORNER * size / UNIT * ss
    draw.rounded_rectangle((0, 0, canvas - 1, canvas - 1), radius=radius, fill=RIM)
    b = border * ss
    draw.rounded_rectangle((b, b, canvas - 1 - b, canvas - 1 - b), radius=max(radius - b, 0), fill=NAVY)
    cells = [(x, y) for y in origins for x in origins]
    for (x, y), color in zip(cells, DOTS):
        draw.ellipse((x * ss, y * ss, (x + diameter) * ss - 1, (y + diameter) * ss - 1), fill=color)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    svg = build_svg()
    for target in (ROOT / "mosaic.svg", ROOT / "frontend" / "public" / "favicon.svg"):
        target.write_text(svg, encoding="utf-8")
    frames = [render(s) for s in ICO_SIZES]
    largest = frames[-1]
    largest.save(ROOT / "mosaic.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    print(f"mosaic.svg + favicon.svg + mosaic.ico ({', '.join(map(str, ICO_SIZES))} px)")


if __name__ == "__main__":
    main()
