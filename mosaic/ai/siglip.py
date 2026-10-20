"""Motore di embedding SigLIP 2 (ONNX INT8): immagini e testi nello stesso spazio vettoriale a 768 dimensioni."""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from tokenizers import Tokenizer

from mosaic.ai.registry import EmbeddingSpec, embedding_spec
from mosaic.ai.runtime import OnnxRuntime
from mosaic.context import Context


class ModelMissing(RuntimeError):
    """I file del modello non sono stati scaricati (vedi `python -m mosaic.assets fetch`)."""


TEXT_BATCH = 32  # prompt per esecuzione del modello di testo


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)


def runtime_for(ctx: Context) -> OnnxRuntime:
    return ctx.service("onnx", lambda: OnnxRuntime(ctx.cfg.ai))


class Siglip:
    def __init__(self, runtime: OnnxRuntime, models_dir: Path, spec: EmbeddingSpec) -> None:
        self.runtime = runtime
        self.spec = spec
        self.dir = Path(models_dir) / spec.dir
        self._tokenizer: Tokenizer | None = None
        self._tok_lock = threading.Lock()

    @classmethod
    def for_context(cls, ctx: Context) -> "Siglip":
        spec = embedding_spec(ctx.models_dir, ctx.cfg.ai.model)
        return cls(runtime_for(ctx), ctx.models_dir, spec)

    @property
    def model_id(self) -> str:
        return self.spec.id

    @property
    def available(self) -> bool:
        return all((self.dir / n).exists() for n in (self.spec.vision, self.spec.text, self.spec.tokenizer))

    def _require(self, name: str) -> Path:
        path = self.dir / name
        if not path.exists():
            raise ModelMissing(f"manca {path}: esegui `python -m mosaic.assets fetch --group core`")
        return path

    # --- immagini -------------------------------------------------------------------
    def preprocess(self, images: Sequence[Image.Image]) -> np.ndarray:
        size = self.spec.image_size
        mean = np.array(self.spec.mean, dtype=np.float32)
        std = np.array(self.spec.std, dtype=np.float32)
        batch = np.empty((len(images), 3, size, size), dtype=np.float32)
        for i, img in enumerate(images):
            arr = np.asarray(img.convert("RGB").resize((size, size), Image.BILINEAR), dtype=np.float32) / 255.0
            batch[i] = ((arr - mean) / std).transpose(2, 0, 1)
        return batch

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        if not images:
            return np.zeros((0, self.spec.dim), dtype=np.float32)
        session = self.runtime.session(self._require(self.spec.vision))
        out = session.run(["pooler_output"], {"pixel_values": self.preprocess(images)})[0]
        return l2_normalize(out)

    def embed_image_files(self, paths: Sequence[Path]) -> np.ndarray:
        images = []
        for path in paths:
            with Image.open(path) as img:
                images.append(img.convert("RGB"))
        return self.embed_images(images)

    # --- testo ----------------------------------------------------------------------
    @property
    def tokenizer(self) -> Tokenizer:
        with self._tok_lock:
            if self._tokenizer is None:
                tok = Tokenizer.from_file(str(self._require(self.spec.tokenizer)))
                tok.enable_truncation(max_length=self.spec.text_length)
                tok.enable_padding(length=self.spec.text_length, pad_id=0, pad_token="<pad>")  # noqa: S106 (token del tokenizer)
                self._tokenizer = tok
            return self._tokenizer

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.spec.dim), dtype=np.float32)
        session = self.runtime.session(self._require(self.spec.text))
        chunks: list[np.ndarray] = []
        # a blocchi: centinaia di prompt in un colpo solo esaurirebbero la memoria di un PC con 8 GB
        for start in range(0, len(texts), TEXT_BATCH):
            # SigLIP è addestrato su testo in minuscolo
            lowered = [t.lower() for t in texts[start:start + TEXT_BATCH]]
            ids = np.array([e.ids for e in self.tokenizer.encode_batch(lowered)], dtype=np.int64)
            chunks.append(session.run(["pooler_output"], {"input_ids": ids})[0])
        return l2_normalize(np.concatenate(chunks))

    # --- punteggio ------------------------------------------------------------------
    def probability(self, cosine: np.ndarray | float) -> np.ndarray | float:
        """Probabilità sigmoide di SigLIP: sigmoid(scala·cos + bias)."""
        z = self.spec.logit_scale * np.asarray(cosine) + self.spec.logit_bias
        return 1.0 / (1.0 + np.exp(-z))


def cosine_to_percent(probability: float) -> int:
    return round(max(0.0, min(1.0, probability)) * 100) if math.isfinite(probability) else 0
