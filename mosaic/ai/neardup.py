"""Quasi-duplicati: pHash (distanza di Hamming) oppure coseno degli embedding, raggruppati con union-find."""

from __future__ import annotations

import numpy as np

from mosaic.ai.vectors import get_index
from mosaic.context import Context

_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def to_signed(value: int) -> int:
    """SQLite salva interi a 64 bit con segno: converte un hash senza segno."""
    return value - (1 << 64) if value >= (1 << 63) else value


def to_unsigned(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def hamming_pairs(hashes: dict[int, int], max_distance: int, chunk: int = 256) -> list[tuple[int, int, int]]:
    """Coppie (a, b, distanza) con distanza di Hamming <= soglia."""
    if len(hashes) < 2:
        return []
    ids = np.fromiter(hashes.keys(), dtype=np.int64)
    values = np.array([to_unsigned(v) for v in hashes.values()], dtype=np.uint64)
    pairs: list[tuple[int, int, int]] = []
    for start in range(0, len(ids), chunk):
        xor = values[start:start + chunk, None] ^ values[None, :]
        counts = _POPCOUNT[xor.view(np.uint8).reshape(*xor.shape, 8)].sum(axis=2)
        rows, cols = np.nonzero(counts <= max_distance)
        for r, c in zip(rows, cols, strict=True):
            a = start + int(r)
            if a < c:
                pairs.append((int(ids[a]), int(ids[c]), int(counts[r, c])))
    return pairs


def group_pairs(pairs: list[tuple[int, int, float]]) -> list[list[int]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0]))


def near_duplicate_groups(ctx: Context) -> list[list[int]]:
    """Gruppi di foto quasi identiche (pHash vicino oppure embedding quasi uguale), esclusa l'archivio."""
    cfg = ctx.cfg.ai
    rows = ctx.db.all("SELECT id, phash FROM photos WHERE phash IS NOT NULL AND deleted_at IS NULL")
    pairs: list[tuple[int, int, float]] = [
        (a, b, float(d)) for a, b, d in hamming_pairs({r["id"]: r["phash"] for r in rows}, cfg.near_duplicate_hamming)]
    index = get_index(ctx)
    if len(index):
        pairs += index.pairwise_above(cfg.near_duplicate_cosine)
    return group_pairs(pairs)
