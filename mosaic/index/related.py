"""Raggruppamento dei file correlati (stack): stesso prefisso base una volta tolte tutte le estensioni note."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mosaic.index.discover import KNOWN_EXT, FileEntry, classify

SEQ_RE = re.compile(r"^(?P<base>.+?)(?:\s\(\d+\)|[\s_-]copy(?:\s\d+)?|\.\d{5})$", re.IGNORECASE)
APPLE_EDIT_RE = re.compile(r"^(?P<head>IMG_)E(?P<num>\d{4,})$", re.IGNORECASE)

# priorità per scegliere il file principale dello stack
_PRIORITY = {"jpeg": 0, "png": 0, "image": 1, "raw": 2, "heic": 3, "video": 5}


def raw_prefix(name: str) -> str:
    """Toglie tutte le estensioni note (IMG_1.jpg.xmp -> IMG_1), conservando maiuscole e minuscole."""
    base = name
    while True:
        suffix = Path(base).suffix
        if suffix and suffix.lower() in KNOWN_EXT:
            base = base[: -len(suffix)]
            continue
        return base


def strip_sequence(prefix: str) -> str:
    match = SEQ_RE.match(prefix)
    return match.group("base") if match else prefix


def group_key(name: str, stack_sequences: bool = False) -> str:
    key = raw_prefix(name)
    if stack_sequences:
        key = strip_sequence(key)
    edit = APPLE_EDIT_RE.match(key)
    if edit:
        key = f"{edit.group('head')}{edit.group('num')}"
    return key.lower()


def stack_key(dir_rel: str, name: str) -> str:
    """Chiave di stack: directory + prefisso base senza sequenze."""
    return f"{dir_rel.lower()}|{group_key(name, False)}"


def stack_key_seq(dir_rel: str, name: str) -> str:
    return f"{dir_rel.lower()}|{group_key(name, True)}"


@dataclass
class Group:
    key: str
    dir: str
    files: list[FileEntry] = field(default_factory=list)

    @property
    def mains(self) -> list[FileEntry]:
        return [f for f in self.files if f.media_type in ("image", "raw", "heic", "video")]

    @property
    def main(self) -> FileEntry | None:
        mains = self.mains
        return min(mains, key=primary_rank) if mains else None


def primary_rank(entry: FileEntry) -> tuple[int, str]:
    ext = entry.ext
    if ext in (".jpg", ".jpeg"):
        rank = _PRIORITY["jpeg"]
    elif ext == ".png":
        rank = _PRIORITY["png"]
    else:
        rank = _PRIORITY.get(entry.media_type, 9)
    return rank, entry.name.lower()


def group_directory(dir_rel: str, entries: list[FileEntry], stack_sequences: bool = False) -> list[Group]:
    """Divide i file di una directory in gruppi di file correlati; scarta i gruppi senza file principale."""
    groups: dict[str, Group] = {}
    for entry in entries:
        if classify(entry.name) is None:
            continue
        key = group_key(entry.name, stack_sequences)
        groups.setdefault(key, Group(key=key, dir=dir_rel)).files.append(entry)
    return [g for g in groups.values() if g.main is not None]
