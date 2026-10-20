"""Etichette zero-shot: il vocabolario (labels_it.yaml) viene confrontato con l'embedding dell'immagine.

Il punteggio è relativo: la similarità con ogni etichetta si standardizza (z-score) sul vocabolario intero e si
trasforma in una confidenza con una sigmoide. Così non dipende da una calibrazione assoluta dei coseni.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from mosaic.ai.siglip import Siglip, l2_normalize
from mosaic.context import Context
from mosaic.meta import sources
from mosaic.textutil import slugify

log = logging.getLogger(__name__)

Z_MID = 3.4   # z a cui la confidenza vale 50%
Z_TAU = 0.45  # ripidità della sigmoide
DEFAULT_PATTERNS = ("una foto di {it}", "a photo of {en}")


@dataclass(frozen=True)
class LabelDef:
    name: str            # nome mostrato (italiano)
    category: str
    prompts: tuple[str, ...]
    threshold: float = 0.0  # confidenza minima specifica (0 = usa la soglia globale)
    priority: int = 0


@dataclass(frozen=True)
class Prediction:
    label: LabelDef
    confidence: float  # 0..1


def load_vocabulary(path: Path) -> list[LabelDef]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    patterns = tuple(data.get("patterns") or DEFAULT_PATTERNS)
    category_threshold = {str(k): float(v) for k, v in (data.get("category_threshold") or {}).items()}
    out: list[LabelDef] = []
    seen: set[str] = set()
    for entry in data.get("labels", []):
        it, en = str(entry["it"]).strip(), str(entry.get("en") or entry["it"]).strip()
        if it.lower() in seen:
            continue
        seen.add(it.lower())
        prompts = tuple(entry.get("prompts") or [p.format(it=it, en=en) for p in patterns])
        out.append(LabelDef(
            name=it.capitalize() if it.islower() else it,
            category=str(entry.get("cat", "")),
            prompts=prompts,
            threshold=float(entry.get("t", category_threshold.get(str(entry.get("cat", "")), 0.0))),
            priority=int(entry.get("p", 0)),
        ))
    if not out:
        raise ValueError(f"vocabolario vuoto: {path}")
    return out


def prune_stale_labels(conn: sqlite3.Connection, keep_names: Iterable[str]) -> int:
    """Toglie le etichette automatiche che non sono più nel vocabolario (quelle manuali restano).

    Ritorna quanti collegamenti foto-etichetta ha rimosso; le etichette rimaste senza foto vengono cancellate.
    """
    keep = {slugify(name) for name in keep_names}
    rows = conn.execute(
        "SELECT DISTINCT l.id, l.slug FROM labels l JOIN photos_labels pl ON pl.label_id=l.id WHERE pl.src=?",
        (sources.ONNX,)).fetchall()
    stale = [int(r["id"]) for r in rows if r["slug"] not in keep]
    removed = 0
    if stale:
        marks = ",".join("?" * len(stale))
        removed = conn.execute(
            f"DELETE FROM photos_labels WHERE src=? AND label_id IN ({marks})", (sources.ONNX, *stale)).rowcount
    conn.execute("DELETE FROM labels WHERE id NOT IN (SELECT label_id FROM photos_labels)")
    return removed


def prune_for_context(ctx: Context, vocabulary_file: str = "labels_it.yaml") -> int:
    """`prune_stale_labels` con il vocabolario installato. Senza il file non tocca nulla."""
    path = ctx.models_dir / vocabulary_file
    if not path.exists():
        return 0
    names = [label.name for label in load_vocabulary(path)]
    with ctx.db.tx() as conn:
        removed = prune_stale_labels(conn, names)
    if removed:
        log.info("etichette fuori vocabolario rimosse: %d", removed)
    return removed


class LabelClassifier:
    def __init__(self, siglip: Siglip, vocabulary: list[LabelDef], cache_dir: Path | None = None) -> None:
        self.siglip = siglip
        self.vocabulary = vocabulary
        self.cache_dir = cache_dir
        self._matrix: np.ndarray | None = None

    def _cache_file(self) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha1(repr([(v.name, v.prompts) for v in self.vocabulary]).encode(), usedforsecurity=False).hexdigest()[:16]
        return self.cache_dir / f"labels_{self.siglip.model_id}_{digest}.npy"

    @property
    def matrix(self) -> np.ndarray:
        """Embedding di ogni etichetta = media normalizzata dei suoi prompt (calcolato una volta e messo in cache)."""
        if self._matrix is None:
            cache = self._cache_file()
            if cache is not None and cache.exists():
                self._matrix = np.load(cache)
            else:
                prompts = [p for v in self.vocabulary for p in v.prompts]
                vectors = self.siglip.embed_texts(prompts)
                rows, pos = [], 0
                for v in self.vocabulary:
                    rows.append(vectors[pos:pos + len(v.prompts)].mean(axis=0))
                    pos += len(v.prompts)
                self._matrix = l2_normalize(np.stack(rows))
                if cache is not None:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    np.save(cache, self._matrix)
        return self._matrix

    def scores(self, image_vectors: np.ndarray) -> np.ndarray:
        """Confidenza (0..1) per ogni etichetta e immagine: matrice (n_immagini, n_etichette)."""
        cos = image_vectors @ self.matrix.T
        z = (cos - cos.mean(axis=1, keepdims=True)) / np.maximum(cos.std(axis=1, keepdims=True), 1e-6)
        return 1.0 / (1.0 + np.exp(-np.clip((z - Z_MID) / Z_TAU, -50, 50)))

    def classify(self, image_vectors: np.ndarray, threshold: float = 0.20, max_labels: int = 5) -> list[list[Prediction]]:
        conf = self.scores(image_vectors)
        results: list[list[Prediction]] = []
        for row in conf:
            order = np.argsort(-row)
            picked: list[Prediction] = []
            for idx in order:
                label = self.vocabulary[int(idx)]
                value = float(row[idx])
                if value < max(threshold, label.threshold):
                    break
                picked.append(Prediction(label, value))
                if len(picked) >= max_labels:
                    break
            results.append(picked)
        return results
