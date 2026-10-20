"""Rilevamento NSFW zero-shot a tre valori: sicuro, non sicuro, non disponibile."""

from __future__ import annotations

from enum import Enum

import numpy as np

from mosaic.ai.siglip import ModelMissing, Siglip

UNSAFE_PROMPTS = (
    "a pornographic photo", "a photo of explicit nudity", "a nsfw image with a naked person",
    "una foto pornografica", "una foto di nudo esplicito",
)
SAFE_PROMPTS = (
    "a photo of a landscape", "a photo of a person wearing clothes", "a family photo", "a photo of food",
    "a photo of a motorcycle", "a photo of a building", "a photo of a car", "a photo of an animal",
    "una foto di un paesaggio", "una foto di persone vestite",
)
SCALE = 100.0       # converte la differenza di coseno in logit
THRESHOLD = 0.80    # probabilità minima per considerare la foto non sicura


class Nsfw(Enum):
    UNAVAILABLE = "unavailable"
    SAFE = "safe"
    UNSAFE = "unsafe"


class NsfwDetector:
    def __init__(self, siglip: Siglip, threshold: float = THRESHOLD) -> None:
        self.siglip = siglip
        self.threshold = threshold
        self._unsafe: np.ndarray | None = None
        self._safe: np.ndarray | None = None

    def _prompts(self) -> tuple[np.ndarray, np.ndarray]:
        if self._unsafe is None or self._safe is None:
            self._unsafe = self.siglip.embed_texts(list(UNSAFE_PROMPTS))
            self._safe = self.siglip.embed_texts(list(SAFE_PROMPTS))
        return self._unsafe, self._safe

    def probability(self, image_vectors: np.ndarray) -> np.ndarray:
        """P(non sicuro) per ogni immagine: sigmoide della differenza tra il miglior prompt unsafe e il miglior safe."""
        unsafe, safe = self._prompts()
        diff = (image_vectors @ unsafe.T).max(axis=1) - (image_vectors @ safe.T).max(axis=1)
        return 1.0 / (1.0 + np.exp(-np.clip(SCALE * diff, -50, 50)))

    def classify(self, image_vectors: np.ndarray) -> list[Nsfw]:
        try:
            probs = self.probability(image_vectors)
        except ModelMissing:
            return [Nsfw.UNAVAILABLE] * len(image_vectors)
        return [Nsfw.UNSAFE if p >= self.threshold else Nsfw.SAFE for p in probs]
