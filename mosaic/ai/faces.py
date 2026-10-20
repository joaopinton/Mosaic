"""Rilevamento (YuNet) e riconoscimento (SFace o ArcFace) dei volti, con embedding L2-normalizzati."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from mosaic.ai.registry import FaceSpec, face_spec
from mosaic.ai.runtime import OnnxRuntime
from mosaic.ai.siglip import ModelMissing, l2_normalize

ARCFACE_SIZE = 112
# Posizione dei 5 punti (occhio sinistro e destro nell'immagine, naso, angoli della bocca) nel ritaglio ArcFace 112x112
ARCFACE_REFERENCE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
# Un volto è "frontale" se il naso sta entro questa frazione della distanza tra gli occhi dal loro punto medio (circa 30°)
MAX_NOSE_OFFSET = 0.35


@dataclass(frozen=True)
class FaceBox:
    x: float            # normalizzati 0..1 sull'immagine, angolo in alto a sinistra
    y: float
    w: float
    h: float
    size: int           # larghezza in pixel dell'originale
    score: int          # 0..100
    landmarks: tuple[tuple[float, float], ...]  # 5 punti normalizzati
    embedding: np.ndarray | None = None


def pil_to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Sovrapposizione (intersezione / area del più piccolo) tra due riquadri [x, y, w, h]."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    smaller = min(a[2] * a[3], b[2] * b[3])
    return (ix * iy) / smaller if smaller > 0 else 0.0


def align_112(bgr: np.ndarray, landmarks: np.ndarray, size: int = ARCFACE_SIZE) -> np.ndarray:
    """Ritaglio allineato (similitudine a 4 gradi di libertà verso il modello ArcFace) dai 5 punti di YuNet in pixel."""
    points = np.asarray(landmarks, dtype=np.float32).reshape(5, 2)
    matrix, _ = cv2.estimateAffinePartial2D(points, ARCFACE_REFERENCE * (size / ARCFACE_SIZE), method=cv2.LMEDS)
    if matrix is None:  # punti degeneri: ritaglio non ruotato attorno al centro dei punti
        matrix = np.array([[1, 0, size / 2 - points[:, 0].mean()], [0, 1, size / 2 - points[:, 1].mean()]], dtype=np.float32)
    return cv2.warpAffine(bgr, matrix, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def frontal(landmarks: Sequence[Sequence[float]], max_offset: float = MAX_NOSE_OFFSET) -> bool:
    """True se il volto guarda l'obiettivo: stima la rotazione laterale dallo scostamento del naso rispetto agli occhi.

    I punti possono essere normalizzati o in pixel: conta solo il rapporto tra differenze orizzontali.
    """
    if len(landmarks) < 3:
        return True  # senza punti non si può escludere
    (ex1, _), (ex2, _), (nx, _) = landmarks[0], landmarks[1], landmarks[2]
    eyes = abs(ex2 - ex1)
    if eyes <= 0:
        return False
    return abs(nx - (ex1 + ex2) / 2) / eyes <= max_offset


def foreground(heights: np.ndarray, short_side: float, scores: np.ndarray, spec: FaceSpec) -> np.ndarray:
    """Maschera dei volti in primo piano (D-22): la folla sullo sfondo non serve a ordinare le foto di un evento.

    Un volto resta se è alto almeno `foreground_size` del lato corto della foto e almeno `foreground_ratio`
    del volto principale. Il volto principale è il più grande tra quelli sicuri (punteggio da clustering), così
    un falso rilevamento grande (una mano, un bicchiere) non fa sparire le persone vere.
    """
    heights = np.asarray(heights, dtype=np.float64)
    if heights.size == 0 or short_side <= 0:
        return np.zeros(heights.shape, dtype=bool)
    rel = heights / short_side
    sure = rel[np.asarray(scores) >= spec.detector.cluster_score]
    main = float(sure.max()) if sure.size else float(rel.max())
    return (rel >= spec.foreground_size) & (rel >= spec.foreground_ratio * main)


class FaceEngine:
    def __init__(self, models_dir: Path, spec: FaceSpec | None = None, runtime: OnnxRuntime | None = None) -> None:
        self.models_dir = Path(models_dir)
        self.spec = spec or face_spec(self.models_dir)
        self._runtime = runtime
        self._detector: cv2.FaceDetectorYN | None = None
        self._recognizer: cv2.FaceRecognizerSF | None = None
        self._lock = threading.RLock()

    @property
    def detector_path(self) -> Path:
        return self.models_dir / self.spec.detector.file

    @property
    def recognizer_path(self) -> Path:
        return self.models_dir / self.spec.recognizer.file

    @property
    def available(self) -> bool:
        return self.detector_path.exists() and self.recognizer_path.exists()

    @property
    def runtime(self) -> OnnxRuntime:
        if self._runtime is None:  # uso fuori dal contesto (script, test): runtime proprio con la configurazione predefinita
            from mosaic.config import AiConfig
            self._runtime = OnnxRuntime(AiConfig())
        return self._runtime

    def _require_models(self) -> None:
        if not self.available:
            raise ModelMissing("modelli dei volti mancanti: esegui `python -m mosaic.assets fetch --group core`")

    def _load(self) -> cv2.FaceDetectorYN:
        self._require_models()
        if self._detector is None:
            det = self.spec.detector
            self._detector = cv2.FaceDetectorYN.create(
                str(self.detector_path), "", (320, 320), score_threshold=0.5, nms_threshold=det.nms, top_k=5000)
        return self._detector

    def _sface(self) -> cv2.FaceRecognizerSF:
        self._require_models()
        if self._recognizer is None:
            self._recognizer = cv2.FaceRecognizerSF.create(str(self.recognizer_path), "")
        return self._recognizer

    def _arcface(self, crops: list[np.ndarray]) -> np.ndarray:
        """Embedding ArcFace di ritagli BGR 112x112: un solo batch (N, 3, 112, 112), RGB normalizzato in [-1, 1]."""
        self._require_models()
        flip = self.spec.recognizer.flip
        batch = list(crops) + [c[:, ::-1] for c in crops] if flip else list(crops)
        blob = (np.stack(batch)[..., ::-1].astype(np.float32) - 127.5) / 127.5
        blob = np.ascontiguousarray(blob.transpose(0, 3, 1, 2))
        session = self.runtime.session(self.recognizer_path)
        out = session.run(None, {session.get_inputs()[0].name: blob})[0]
        if flip:
            out = out[:len(crops)] + out[len(crops):]
        return l2_normalize(out)

    def embed_rows(self, bgr: np.ndarray, rows: np.ndarray) -> np.ndarray:
        """Embedding L2-normalizzati dei volti `rows` (N×15, pixel di `bgr`, formato YuNet) con il riconoscitore attivo."""
        if len(rows) == 0:
            return np.zeros((0, self.spec.recognizer.dim), dtype=np.float32)
        rows = np.asarray(rows, dtype=np.float32)
        with self._lock:
            if self.spec.recognizer.engine == "arcface":
                return self._arcface([align_112(bgr, r[4:14], self.spec.recognizer.input_size) for r in rows])
            recognizer = self._sface()
            return l2_normalize(np.stack([
                recognizer.feature(recognizer.alignCrop(bgr, r.reshape(1, 15))).reshape(-1) for r in rows]))

    def _detect_rows(self, bgr: np.ndarray, floor: int) -> np.ndarray:
        detector = self._load()
        height, width = bgr.shape[:2]
        scale = min(1.0, self.spec.detector.input_size / max(height, width))
        small = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale)))) if scale < 1 else bgr
        detector.setInputSize((small.shape[1], small.shape[0]))
        detector.setScoreThreshold(floor / 100)
        _, rows = detector.detect(small)
        if rows is None:
            return np.zeros((0, 15), dtype=np.float32)
        rows = rows.copy()
        rows[:, :14] /= scale  # coordinate nell'immagine di partenza
        return rows

    def detect(self, detect_img: Image.Image, original_width: int, embed_img: Image.Image | None = None) -> list[FaceBox]:
        """Trova i volti su `detect_img` (di solito fit_720) e ne calcola l'embedding da `embed_img` (più grande)."""
        with self._lock:
            bgr = pil_to_bgr(detect_img)
            height, width = bgr.shape[:2]
            rows = self._detect_rows(bgr, self.spec.detector.score_floor)

            def keep(min_size: int, rows_: np.ndarray) -> np.ndarray:
                sizes = rows_[:, 2] / width * original_width
                return rows_[sizes >= min_size]

            kept = keep(self.spec.size, rows)
            if len(kept) == 0:  # secondo passaggio con volti più piccoli, solo se il primo non trova nulla
                kept = keep(self.spec.size_retry, rows)
            kept = self._dedupe(kept)
            if len(kept):
                kept = kept[foreground(kept[:, 3], min(width, height), kept[:, 14] * 100, self.spec)]
            big = pil_to_bgr(embed_img) if embed_img is not None else bgr
            ratio = big.shape[1] / width
            return self._boxes(kept, big, ratio, width, height, original_width)

    def _dedupe(self, rows: np.ndarray) -> np.ndarray:
        if len(rows) < 2:
            return rows
        order = np.argsort(-rows[:, 14])
        kept: list[np.ndarray] = []
        for idx in order:
            if all(overlap(rows[idx, :4], k[:4]) <= self.spec.detector.overlap for k in kept):
                kept.append(rows[idx])
        return np.stack(kept)

    def _boxes(self, rows: np.ndarray, big: np.ndarray, ratio: float, width: int, height: int, original_width: int) -> list[FaceBox]:
        """FaceBox per ogni riga YuNet (coordinate di `width`×`height`); gli embedding sono calcolati da `big` in un batch."""
        if len(rows) == 0:
            return []
        scaled = (rows * np.array([ratio] * 14 + [1.0], dtype=np.float32)).astype(np.float32)
        embeddings = self.embed_rows(big, scaled)
        boxes = []
        for row, embedding in zip(rows, embeddings):
            x, y, w, h = row[:4]
            lm = tuple((float(row[4 + 2 * i] / width), float(row[5 + 2 * i] / height)) for i in range(5))
            boxes.append(FaceBox(
                x=float(max(0.0, x / width)), y=float(max(0.0, y / height)), w=float(min(1.0, w / width)),
                h=float(min(1.0, h / height)), size=round(w / width * original_width),
                score=round(float(row[14]) * 100), landmarks=lm, embedding=embedding))
        return boxes

    def _box(self, row: np.ndarray, big: np.ndarray, ratio: float, width: int, height: int, original_width: int) -> FaceBox:
        return self._boxes(row.reshape(1, 15), big, ratio, width, height, original_width)[0]
