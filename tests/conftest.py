from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mosaic.config import Config  # noqa: E402
from mosaic.context import Context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HAS_EXIFTOOL = (ROOT / "runtime" / "exiftool" / "exiftool.exe").exists()
HAS_FFMPEG = (ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe").exists()
HAS_SIGLIP = (ROOT / "models" / "siglip2-base-224" / "vision_model_int8.onnx").exists()
HAS_FACES = (ROOT / "models" / "faces" / "face_detection_yunet_2023mar.onnx").exists()
HAS_ARCFACE = (ROOT / "models" / "faces" / "w600k_r50.onnx").exists()

needs_exiftool = pytest.mark.skipif(not HAS_EXIFTOOL, reason="exiftool non installato")
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg non installato")
needs_siglip = pytest.mark.skipif(not HAS_SIGLIP, reason="modello SigLIP 2 non scaricato")
needs_faces = pytest.mark.skipif(not HAS_FACES, reason="modelli dei volti non scaricati")
needs_arcface = pytest.mark.skipif(not HAS_ARCFACE, reason="modello ArcFace non scaricato")


def make_jpeg(path: Path, color: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


def make_png(path: Path, color: tuple[int, int, int] = (10, 120, 200), size: tuple[int, int] = (32, 32)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def make_video(path: Path, seconds: int = 2, codec: str = "libx264", size: str = "160x120") -> Path:
    """Genera un video di prova con ffmpeg (pattern testsrc)."""
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"), "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=size={size}:rate=10", "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", codec, str(path)],
        check=True, capture_output=True,
    )
    return path


def write_tags(path: Path, **tags: str) -> None:
    """Scrive tag con exiftool (GPS, titolo, ecc.) in un file di prova."""
    import subprocess

    args = [str(ROOT / "runtime" / "exiftool" / "exiftool.exe"), "-overwrite_original", "-q"]
    args += [f"-{k}={v}" for k, v in tags.items()] + [str(path)]
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    lib.mkdir()
    return lib


@pytest.fixture()
def ctx(tmp_path: Path, library: Path):
    cfg = Config(library_roots=(str(library),), data_dir=str(tmp_path / "data"), workers=2)
    context = Context.open(cfg)
    yield context
    context.close()


os.environ.setdefault("PYTHONUTF8", "1")
