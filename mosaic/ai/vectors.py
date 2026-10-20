"""Indice vettoriale in memoria (matrice numpy) caricato dalla tabella `embeddings`, dietro un'interfaccia sostituibile."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Sequence
from typing import Protocol

import numpy as np

from mosaic.context import Context
from mosaic.db import Database


def encode_vector(vec: np.ndarray) -> bytes:
    """float16 su disco: circa la metà dello spazio con perdita di precisione trascurabile per il coseno."""
    return np.asarray(vec, dtype=np.float16).tobytes()


def decode_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def save_embeddings(conn: sqlite3.Connection, model_id: str, ids: Sequence[int], vectors: np.ndarray) -> None:
    conn.executemany(
        "INSERT INTO embeddings(photo_id, model_id, dim, vector) VALUES (?,?,?,?) "
        "ON CONFLICT(photo_id, model_id) DO UPDATE SET dim=excluded.dim, vector=excluded.vector",
        [(int(i), model_id, int(vectors.shape[1]), encode_vector(v)) for i, v in zip(ids, vectors, strict=True)],
    )


class VectorIndexLike(Protocol):
    def search(self, query: np.ndarray, k: int, allowed: Iterable[int] | None = None) -> tuple[np.ndarray, np.ndarray]: ...
    def __len__(self) -> int: ...


class VectorIndex:
    """Ricerca esatta per prodotto scalare (i vettori sono L2-normalizzati, quindi coseno)."""

    def __init__(self, ids: np.ndarray, matrix: np.ndarray, model_id: str = "") -> None:
        self.ids = ids.astype(np.int64)
        self.matrix = matrix.astype(np.float32)
        self.model_id = model_id
        self._pos = {int(i): n for n, i in enumerate(self.ids)}

    @classmethod
    def empty(cls, dim: int = 768, model_id: str = "") -> "VectorIndex":
        return cls(np.zeros(0, dtype=np.int64), np.zeros((0, dim), dtype=np.float32), model_id)

    @classmethod
    def load(cls, db: Database, model_id: str) -> "VectorIndex":
        rows = db.all(
            "SELECT e.photo_id, e.vector FROM embeddings e JOIN photos p ON p.id=e.photo_id "
            "WHERE e.model_id=? AND p.deleted_at IS NULL ORDER BY e.photo_id",
            (model_id,),
        )
        if not rows:
            return cls.empty(model_id=model_id)
        ids = np.fromiter((r["photo_id"] for r in rows), dtype=np.int64, count=len(rows))
        matrix = np.stack([decode_vector(r["vector"]) for r in rows])
        return cls(ids, matrix, model_id)

    def __len__(self) -> int:
        return len(self.ids)

    def __contains__(self, photo_id: int) -> bool:
        return int(photo_id) in self._pos

    def vector(self, photo_id: int) -> np.ndarray | None:
        pos = self._pos.get(int(photo_id))
        return None if pos is None else self.matrix[pos]

    def search(self, query: np.ndarray, k: int, allowed: Iterable[int] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Ritorna (id, coseno) dei k vettori più vicini, in ordine decrescente. `allowed` limita i candidati."""
        if len(self) == 0 or k <= 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        if allowed is not None:
            allowed_ids = np.fromiter((int(a) for a in allowed), dtype=np.int64)
            rows = np.nonzero(np.isin(self.ids, allowed_ids))[0]
            if rows.size == 0:
                return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
            scores = self.matrix[rows] @ q
            ids = self.ids[rows]
        else:
            scores = self.matrix @ q
            ids = self.ids
        k = min(k, scores.size)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return ids[top], scores[top]

    def pairwise_above(self, threshold: float, chunk: int = 512) -> list[tuple[int, int, float]]:
        """Coppie (a, b, coseno) con coseno >= soglia, per trovare i quasi-duplicati. Calcolo a blocchi."""
        pairs: list[tuple[int, int, float]] = []
        n = len(self)
        for start in range(0, n, chunk):
            block = self.matrix[start:start + chunk] @ self.matrix.T
            rows, cols = np.nonzero(block >= threshold)
            for r, c in zip(rows, cols, strict=True):
                a = start + int(r)
                if a < c:
                    pairs.append((int(self.ids[a]), int(self.ids[c]), float(block[r, c])))
        return pairs


_cache_lock = threading.Lock()


def get_index(ctx: Context, model_id: str | None = None) -> VectorIndex:
    """Indice condiviso nel contesto; si ricarica solo se la tabella `embeddings` è cambiata."""
    model = model_id or ctx.cfg.ai.model
    stamp = ctx.db.one(
        "SELECT COUNT(*) AS n, COALESCE(MAX(rowid),0) AS m FROM embeddings WHERE model_id=?", (model,)
    )
    key = (stamp["n"], stamp["m"])
    with _cache_lock:
        cached = ctx.service(f"vectors:{model}", lambda: {"key": None, "index": None})
        if cached["key"] != key or cached["index"] is None:
            cached["index"] = VectorIndex.load(ctx.db, model)
            cached["key"] = key
        return cached["index"]
