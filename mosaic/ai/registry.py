"""Registro dei modelli: legge models/models.yaml e fornisce specifiche tipizzate (con valori predefiniti nel codice)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EmbeddingSpec:
    id: str = "siglip2-base-224"
    dir: str = "siglip2-base-224"
    vision: str = "vision_model_int8.onnx"
    text: str = "text_model_int8.onnx"
    tokenizer: str = "tokenizer.json"
    dim: int = 768
    image_size: int = 224
    mean: tuple[float, float, float] = (0.5, 0.5, 0.5)
    std: tuple[float, float, float] = (0.5, 0.5, 0.5)
    text_length: int = 64
    logit_scale: float = 117.0
    logit_bias: float = -12.0


@dataclass(frozen=True)
class DetectorSpec:
    id: str = "yunet-2023mar"
    file: str = "faces/face_detection_yunet_2023mar.onnx"
    input_size: int = 640
    score_floor: int = 65
    cluster_score: int = 85
    nms: float = 0.3
    overlap: float = 0.42


@dataclass(frozen=True)
class RecognizerSpec:
    id: str = "sface-2021dec"
    file: str = "faces/face_recognition_sface_2021dec.onnx"
    dim: int = 128
    engine: str = "sface"          # "sface" (OpenCV) oppure "arcface" (ONNX tramite il runtime condiviso)
    input_size: int = 112          # lato del ritaglio allineato dato al modello
    flip: bool = False             # media tra il ritaglio e il suo specchio (solo arcface)
    cluster_dist: float = 0.72     # raggio di vicinato del DBSCAN
    cluster_radius: float = 0.70   # tetto del raggio di un cluster
    cluster_slack: float = 0.25    # margine oltre il raggio entro cui un volto entra in un cluster esistente
    match_dist: float = 0.25       # distanza dai volti nominati sotto cui la persona si assegna da sola
    match_margin: float = 0.01     # vantaggio minimo sulla seconda persona (o sul secondo cluster di un'altra persona)
    max_match_dist: float = 1.25   # oltre questa distanza non c'è nemmeno un suggerimento


@dataclass(frozen=True)
class FaceSpec:
    detector: DetectorSpec = field(default_factory=DetectorSpec)
    recognizer: RecognizerSpec = field(default_factory=RecognizerSpec)
    size: int = 25
    size_retry: int = 10
    cluster_size: int = 112
    cluster_core: int = 5
    cluster_core_retry: int = 4
    # primo piano (D-22): altezza minima del volto sul lato corto della foto, e rispetto al volto principale
    foreground_size: float = 0.07
    foreground_ratio: float = 0.5


@dataclass(frozen=True)
class LlmSpec:
    id: str = "qwen3vl-2b"
    dir: str = "llm"
    model: str = "Qwen3VL-2B-Instruct-Q4_K_M.gguf"
    mmproj: str = "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"
    server: str = "runtime/llama"


def _pick(cls: type, data: dict[str, Any]) -> Any:
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    for key in ("mean", "std"):
        if key in known and isinstance(known[key], list):
            known[key] = tuple(known[key])
    return cls(**known)


def _load(models_dir: Path) -> dict[str, Any]:
    path = models_dir / "models.yaml"
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "models" / "models.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def embedding_spec(models_dir: Path, model_id: str) -> EmbeddingSpec:
    entry = (_load(models_dir).get("embedding") or {}).get(model_id)
    if entry is None:
        if model_id != EmbeddingSpec().id:
            raise KeyError(f"modello di embedding non registrato: {model_id}")
        return EmbeddingSpec()
    return _pick(EmbeddingSpec, {"id": model_id, **entry})


def face_spec(models_dir: Path) -> FaceSpec:
    data = _load(models_dir).get("faces") or {}
    return FaceSpec(
        detector=_pick(DetectorSpec, data.get("detector") or {}),
        recognizer=_pick(RecognizerSpec, data.get("recognizer") or {}),
        **{k: data[k] for k in ("size", "size_retry", "cluster_size", "cluster_core", "cluster_core_retry",
                                    "foreground_size", "foreground_ratio") if k in data},
    )


def llm_spec(models_dir: Path, model_id: str) -> LlmSpec:
    entry = (_load(models_dir).get("llm") or {}).get(model_id)
    if entry is None:
        if model_id != LlmSpec().id:
            raise KeyError(f"modello LLM non registrato: {model_id}")
        return LlmSpec()
    return _pick(LlmSpec, {"id": model_id, **entry})
