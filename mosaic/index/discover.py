"""Scoperta dei file: scansione ricorsiva, regole di esclusione e controllo dei magic bytes."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
HEIC_EXT = {".heic", ".heif", ".avif"}
RAW_EXT = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw", ".3fr", ".rwl"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv"}
SIDECAR_EXT = {".xmp", ".json"}
KNOWN_EXT = IMAGE_EXT | HEIC_EXT | RAW_EXT | VIDEO_EXT | SIDECAR_EXT

IGNORE_FILE = ".mosaicignore"
STORAGE_MARKER = ".mosaicstorage"
SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", "@eadir", "__macosx"}

# famiglia di formato attesa per estensione (solo per i formati con una firma affidabile)
EXT_FAMILY = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp", ".bmp": "bmp",
    ".heic": "heic", ".heif": "heic", ".avif": "heic",
    ".tif": "tiff", ".tiff": "tiff",
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mp4", ".3gp": "mp4",
}


@dataclass(frozen=True)
class FileEntry:
    name: str
    dir: str            # directory relativa alla radice, con "/" ('' = radice)
    abs_path: Path
    size: int
    mtime: float

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def media_type(self) -> str:
        return classify(self.name) or ""


def classify(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in HEIC_EXT:
        return "heic"
    if ext in RAW_EXT:
        return "raw"
    if ext in VIDEO_EXT:
        return "video"
    if ext in SIDECAR_EXT:
        return "sidecar"
    return None


def sniff_family(header: bytes) -> str | None:
    """Riconosce la famiglia di formato dai primi byte; None se sconosciuta."""
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"GIF8":
        return "gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[:2] == b"BM":
        return "bmp"
    if header[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        return "heic" if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif", b"heim", b"heis") else "mp4"
    return None


def extension_matches(path: Path) -> bool:
    """Falso solo quando l'estensione promette un formato e il contenuto è chiaramente un altro."""
    expected = EXT_FAMILY.get(path.suffix.lower())
    if expected is None:
        return True
    try:
        with path.open("rb") as fh:
            actual = sniff_family(fh.read(16))
    except OSError:
        return False
    if actual is None:
        return True
    if expected == "tiff" and actual == "tiff":
        return True
    return actual == expected


def _ignore_patterns(directory: Path, inherited: tuple[str, ...]) -> tuple[str, ...]:
    ignore = directory / IGNORE_FILE
    if not ignore.is_file():
        return inherited
    try:
        lines = ignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return inherited
    own = tuple(line.strip() for line in lines if line.strip() and not line.startswith("#"))
    return inherited + own


def _ignored(rel: str, name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in patterns)


def iter_directories(root: Path, exclude: tuple[Path, ...] = ()) -> Iterator[tuple[str, list[FileEntry]]]:
    """Visita ricorsivamente la radice e produce (directory relativa, file della directory).

    Salta cartelle nascoste, di sistema, marcate con .mosaicstorage o elencate in .mosaicignore, e
    protegge dai cicli di link simbolici.
    """
    root = root.resolve()
    excluded = {p.resolve() for p in exclude}
    visited: set[Path] = set()
    stack: list[tuple[Path, str, tuple[str, ...]]] = [(root, "", ())]
    while stack:
        directory, rel_dir, inherited = stack.pop()
        try:
            real = directory.resolve()
        except OSError:
            continue
        if real in visited or real in excluded:
            continue
        visited.add(real)
        patterns = _ignore_patterns(directory, inherited)
        files: list[FileEntry] = []
        subdirs: list[tuple[Path, str]] = []
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_dir():
                    lowered = entry.name.lower()
                    if entry.name.startswith(".") or lowered in SKIP_DIR_NAMES or _ignored(rel, entry.name, patterns):
                        continue
                    if (Path(entry.path) / STORAGE_MARKER).exists():
                        continue
                    subdirs.append((Path(entry.path), rel))
                elif entry.is_file():
                    if entry.name.startswith(".") or _ignored(rel, entry.name, patterns):
                        continue
                    if classify(entry.name) is None:
                        continue
                    st = entry.stat()
                    files.append(FileEntry(entry.name, rel_dir, Path(entry.path), st.st_size, st.st_mtime))
            except OSError:
                continue
        if files:
            yield rel_dir, files
        for sub, rel in reversed(subdirs):
            stack.append((sub, rel, patterns))
