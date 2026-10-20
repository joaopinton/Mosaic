"""Hash del contenuto: SHA-1 (identità e cache) e SHA-256 (compatibilità col vecchio catalogo) in una sola lettura."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

BLOCK = 1 << 20


@dataclass(frozen=True)
class FileHash:
    sha1: str
    sha256: str
    size: int


def hash_file(path: Path) -> FileHash:
    sha1 = hashlib.sha1(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(BLOCK), b""):
            sha1.update(block)
            sha256.update(block)
            size += len(block)
    return FileHash(sha1.hexdigest(), sha256.hexdigest(), size)
