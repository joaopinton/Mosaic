"""Fotogramma poster di un video, usato come anteprima per miniature, embedding e volti."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mosaic.paths import tool
from mosaic.video.probe import NO_WINDOW

POSTER_FRACTION = 0.1
POSTER_MAX_SECONDS = 10.0


def poster_time(duration: float) -> float:
    return min(max(duration, 0.0) * POSTER_FRACTION, POSTER_MAX_SECONDS)


def extract_poster(video: Path, dest: Path, duration: float = 0.0, ffmpeg: Path | None = None) -> bool:
    """Estrae un fotogramma JPEG; ritorna True se il file è stato creato."""
    exe = ffmpeg or tool("ffmpeg")
    if exe is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp.jpg")
    for seek in (poster_time(duration), 0.0):  # se il punto scelto fallisce, riprova dal primo fotogramma
        cmd = [str(exe), "-v", "error", "-y", "-ss", f"{seek:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(tmp)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=False, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dest)
            return True
    tmp.unlink(missing_ok=True)
    return False
