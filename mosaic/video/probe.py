"""ffprobe: durata, codec, risoluzione e rotazione dei video."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mosaic.paths import tool

NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    vcodec: str
    acodec: str
    container: str
    bitrate: int
    pix_fmt: str = ""


def _fps(text: str) -> float:
    try:
        value = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return round(value, 3) if 0 < value < 1000 else 0.0


def _rotation(stream: dict) -> int:
    rotate = (stream.get("tags") or {}).get("rotate")
    if rotate is not None:
        try:
            return int(rotate) % 360
        except ValueError:
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(side["rotation"]) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe(path: Path, ffprobe: Path | None = None) -> VideoInfo | None:
    exe = ffprobe or tool("ffprobe")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [str(exe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=60, check=False, creationflags=NO_WINDOW,
        )
        data = json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    if video is None:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if _rotation(video) in (90, 270):
        width, height = height, width
    try:
        duration = float(video.get("duration") or fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    try:
        bitrate = int(fmt.get("bit_rate") or video.get("bit_rate") or 0)
    except ValueError:
        bitrate = 0
    return VideoInfo(
        duration=round(duration, 3), width=width, height=height,
        fps=_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"),
        vcodec=str(video.get("codec_name", "")), acodec=str(audio.get("codec_name", "")),
        container=str(fmt.get("format_name", "")), bitrate=bitrate, pix_fmt=str(video.get("pix_fmt", "")),
    )
