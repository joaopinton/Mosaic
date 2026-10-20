"""Transcodifica H.264/AAC in MP4 con faststart, solo quando il browser non riuscirebbe a riprodurre l'originale."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from mosaic.context import Context
from mosaic.paths import tool
from mosaic.video.probe import NO_WINDOW, VideoInfo

log = logging.getLogger(__name__)

MAX_BITRATE = 60_000_000   # 60 Mbps, come PhotoPrism
MAX_SIDE = 1920
WEB_AUDIO = {"aac", "mp3", "opus", "vorbis", ""}
WEB_PIX_FMT = {"yuv420p", "yuvj420p", ""}
SCALE = f"scale='if(gt(iw,ih),min({MAX_SIDE},iw),-2)':'if(gt(iw,ih),-2,min({MAX_SIDE},ih))'"


def transcoded_path(ctx: Context, sha1: str) -> Path:
    return ctx.video_dir / f"{sha1}.mp4"


def is_web_playable(info: VideoInfo, ext: str) -> bool:
    """True se un browser moderno riproduce il file così com'è."""
    if info.bitrate > MAX_BITRATE:
        return False
    ext = ext.lower()
    if ext in (".mp4", ".m4v", ".mov"):
        return info.vcodec == "h264" and info.acodec in WEB_AUDIO and info.pix_fmt in WEB_PIX_FMT
    if ext == ".webm":
        return info.vcodec in ("vp8", "vp9", "av1")
    return False


def build_command(ffmpeg: Path, src: Path, dest: Path) -> list[str]:
    return [
        str(ffmpeg), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-vf", SCALE,
        "-maxrate", "60M", "-bufsize", "120M", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ]


def _run(cmd: list[str], timeout: float, cancel: threading.Event | None) -> tuple[int, bytes]:
    """Esegue ffmpeg controllando ogni 0,2 s se l'utente ha annullato: in quel caso il processo si chiude subito."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if (cancel is not None and cancel.wait(0.2)) or time.monotonic() > deadline:
                proc.kill()
                proc.wait(5)
                raise subprocess.TimeoutExpired(cmd, timeout) if time.monotonic() > deadline else InterruptedError("annullato")
            if cancel is None:
                time.sleep(0.2)
        return proc.returncode, (proc.stderr.read() if proc.stderr else b"")
    finally:
        if proc.stderr:
            proc.stderr.close()


def transcode(ctx: Context, src: Path, sha1: str, duration: float = 0.0, ffmpeg: Path | None = None,
              cancel: threading.Event | None = None) -> Path | None:
    """Crea (una sola volta) la versione web del video. Ritorna il percorso oppure None se ffmpeg fallisce."""
    dest = transcoded_path(ctx, sha1)
    if dest.exists():
        return dest
    exe = ffmpeg or tool("ffmpeg")
    if exe is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp.mp4")
    timeout = max(600.0, duration * 20)
    try:
        code, stderr = _run(build_command(exe, src, tmp), timeout, cancel)
    except (OSError, subprocess.TimeoutExpired, InterruptedError) as exc:
        log.warning("transcodifica interrotta per %s: %s", src.name, exc)
        tmp.unlink(missing_ok=True)
        return None
    if code != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        log.warning("ffmpeg ha fallito su %s: %s", src.name, stderr.decode("utf-8", "replace")[-300:])
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(dest)
    return dest
