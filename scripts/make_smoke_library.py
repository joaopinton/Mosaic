"""Crea una piccola libreria mista per la verifica end-to-end: `python scripts/make_smoke_library.py <cartella> [sorgenti...]`.

Le immagini di base vengono copiate da una cartella di foto qualunque (di default le immagini di Windows); i video sono
generati con ffmpeg. Nessun dato personale entra nel repository.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
DEFAULT_SOURCES = [Path("C:/Windows/Web/Screen"), Path("C:/Windows/Web/Wallpaper")]


def source_images(folders: list[Path]) -> list[Path]:
    found: list[Path] = []
    for folder in folders:
        found += sorted(folder.rglob("*.jpg"))
    return found


def video(path: Path, seconds: int, codec: str = "libx264") -> None:
    subprocess.run(
        [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", str(seconds),
         "-pix_fmt", "yuv420p", "-c:v", codec, str(path)],
        check=True, capture_output=True,
    )


def main(target: Path, folders: list[Path]) -> None:
    images = source_images(folders)
    if len(images) < 19:
        raise SystemExit(f"servono almeno 19 immagini JPEG distinte in {folders}")
    target.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(images[:14], 1):
        shutil.copy(src, target / f"IMG_{i:04d}.jpg")
    # HEIC (se l'encoder è disponibile), PNG e GIF animata
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        for i, src in enumerate(images[14:17], 1):
            Image.open(src).convert("RGB").save(target / f"HEIC_{i:04d}.heic", quality=80)
    except Exception as exc:  # encoder HEIF assente: si prosegue senza
        print(f"HEIC saltato: {exc}")
    Image.open(images[0]).convert("RGB").resize((640, 360)).save(target / "grafica.png")
    frames = [Image.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(target / "animata.gif", save_all=True, append_images=frames[1:], duration=120, loop=0)
    # video: uno riproducibile e uno da convertire; Live Photo = JPEG + breve MOV con lo stesso nome
    video(target / "VID_0001.mp4", 3)
    video(target / "VID_0002.mp4", 2, codec="mpeg4")
    shutil.copy(images[17], target / "IMG_0100.jpg")
    video(target / "IMG_0100.mov", 2)
    # duplicato, sidecar XMP e stack con un RAW non leggibile
    (target / "copie").mkdir(exist_ok=True)
    shutil.copy(target / "IMG_0001.jpg", target / "copie" / "copia_di_IMG_0001.jpg")
    (target / "IMG_0002.jpg.xmp").write_text(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title><rdf:Alt><rdf:li xml:lang="x-default">Il mio titolo XMP</rdf:li>'
        "</rdf:Alt></dc:title></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>", encoding="utf-8")
    shutil.copy(images[18], target / "RAW_0001.jpg")
    (target / "RAW_0001.CR2").write_bytes(b"II*\x00" + b"non-un-vero-raw" * 40)
    count = sum(1 for p in target.rglob("*") if p.is_file())
    print(f"libreria di prova pronta: {count} file in {target}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "smoke_library"
    main(out, [Path(p) for p in sys.argv[2:]] or DEFAULT_SOURCES)
