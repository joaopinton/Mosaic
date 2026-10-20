"""Wrapper di exiftool in modalità -stay_open (un solo processo riusato), con cache JSON per hash e fallback Pillow."""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

from mosaic.paths import tool

log = logging.getLogger(__name__)

READY = b"{ready}"
TIMEOUT = 60.0
CACHE_LIMIT = 1 << 20  # 1 MiB, come PhotoPrism
BASE_ARGS = ["-json", "-n", "-charset", "filename=utf8", "-charset", "exif=utf8", "-api", "largefilesupport=1"]


class ExifToolError(RuntimeError):
    pass


class ExifTool:
    """Un processo exiftool persistente. Thread-safe: le richieste sono serializzate."""

    def __init__(self, exe: Path | None = None) -> None:
        self.exe = exe or tool("exiftool")
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.exe is not None and Path(self.exe).exists()

    def _start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        self._proc = subprocess.Popen(
            [str(self.exe), "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._lines), daemon=True).start()

    @staticmethod
    def _pump(proc: "subprocess.Popen[bytes]", out: "queue.Queue[bytes | None]") -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, b""):
            out.put(line.rstrip(b"\r\n"))
        out.put(None)

    def _run(self, args: list[str]) -> bytes:
        if not self.available:
            raise ExifToolError("exiftool non disponibile")
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            assert self._proc is not None and self._proc.stdin is not None
            payload = "\n".join([*args, "-execute"]) + "\n"
            try:
                self._proc.stdin.write(payload.encode("utf-8"))
                self._proc.stdin.flush()
            except OSError as exc:
                self._kill()
                raise ExifToolError(f"exiftool non risponde: {exc}") from exc
            chunks: list[bytes] = []
            while True:
                try:
                    line = self._lines.get(timeout=TIMEOUT)
                except queue.Empty:
                    self._kill()
                    raise ExifToolError("timeout di exiftool") from None
                if line is None:
                    self._kill()
                    raise ExifToolError("exiftool si è chiuso")
                if line == READY:
                    return b"\n".join(chunks)
                chunks.append(line)

    def _kill(self) -> None:
        if self._proc is not None:
            with contextlib.suppress(OSError):
                self._proc.kill()
            self._proc = None

    def read(self, path: Path) -> dict[str, Any]:
        """Metadati di un file come dizionario piatto (valori numerici grazie a -n)."""
        raw = self._run([*BASE_ARGS, str(path)])
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ExifToolError(f"risposta non valida per {path.name}: {exc}") from exc
        return data[0] if data else {}

    def write(self, path: Path, tags: dict[str, str]) -> None:
        args = ["-overwrite_original", "-charset", "filename=utf8", *[f"-{k}={v}" for k, v in tags.items()], str(path)]
        out = self._run(args).decode("utf-8", errors="replace")
        if "1 image files updated" not in out and "1 files updated" not in out:
            raise ExifToolError(f"scrittura fallita su {path.name}: {out.strip()}")

    def binary(self, path: Path, tag: str) -> bytes:
        """Estrae un tag binario (anteprime, video incorporato). Usa un processo a parte per non mischiare i byte."""
        if not self.available:
            return b""
        result = subprocess.run(
            [str(self.exe), "-b", f"-{tag}", "-charset", "filename=utf8", str(path)],
            capture_output=True, timeout=120, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout if result.returncode == 0 else b""

    def close(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write(b"-stay_open\nFalse\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self._kill()
            self._proc = None


# --- cache e fallback -----------------------------------------------------------------

def cache_file(cache_dir: Path, sha1: str) -> Path:
    return cache_dir / sha1[:2] / f"{sha1}.json"


def read_cached(cache_dir: Path, sha1: str) -> dict[str, Any] | None:
    path = cache_file(cache_dir, sha1)
    try:
        if path.exists() and path.stat().st_size <= CACHE_LIMIT:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def write_cached(cache_dir: Path, sha1: str, data: dict[str, Any]) -> None:
    path = cache_file(cache_dir, sha1)
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > CACHE_LIMIT:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ratio(value: Any) -> float | None:
    try:
        return float(Fraction(value.numerator, value.denominator)) if hasattr(value, "numerator") else float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def pillow_exif(path: Path) -> dict[str, Any]:
    """Fallback senza exiftool: legge i tag EXIF principali con Pillow e li rinomina come exiftool."""
    from PIL import ExifTags, Image

    out: dict[str, Any] = {}
    try:
        with Image.open(path) as img:
            out["ImageWidth"], out["ImageHeight"] = img.size
            exif = img.getexif()
            base = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
            sub = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.get_ifd(0x8769).items()}
            gps = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in exif.get_ifd(0x8825).items()}
    except Exception as exc:  # file danneggiato o formato non supportato
        log.debug("fallback EXIF fallito per %s: %s", path, exc)
        return out
    merged = {**base, **sub}
    for key in ("Make", "Model", "LensModel", "DateTimeOriginal", "DateTime", "Orientation", "OffsetTimeOriginal"):
        if key in merged:
            out[key] = str(merged[key]).strip("\x00 ") if isinstance(merged[key], (str, bytes)) else merged[key]
    if "DateTime" in out and "ModifyDate" not in out:
        out["ModifyDate"] = out.pop("DateTime")
    if "ISOSpeedRatings" in merged:
        iso = merged["ISOSpeedRatings"]
        out["ISO"] = int(iso[0] if isinstance(iso, tuple) else iso)
    for src, dst in (("ExposureTime", "ExposureTime"), ("FNumber", "FNumber"), ("FocalLength", "FocalLength")):
        if src in merged and (val := _ratio(merged[src])) is not None:
            out[dst] = val
    try:
        lat, lng = _gps_degrees(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")), _gps_degrees(
            gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat is not None and lng is not None:
            out["GPSLatitude"], out["GPSLongitude"] = lat, lng
    except (TypeError, ValueError):
        pass
    return out


def _gps_degrees(value: Any, ref: Any) -> float | None:
    if not value or len(value) != 3:
        return None
    deg, minutes, seconds = (_ratio(v) or 0.0 for v in value)
    result = deg + minutes / 60 + seconds / 3600
    return -result if ref in ("S", "W") else result
