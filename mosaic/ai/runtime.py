"""Sessioni ONNX Runtime caricate in modo lazy e scaricabili (con 8 GB di RAM non si tiene tutto in memoria)."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import onnxruntime as ort

from mosaic.config import AiConfig

log = logging.getLogger(__name__)


class OnnxRuntime:
    def __init__(self, cfg: AiConfig) -> None:
        self.cfg = cfg
        self._sessions: dict[Path, ort.InferenceSession] = {}
        self._lock = threading.Lock()

    @property
    def providers(self) -> list[str]:
        available = ort.get_available_providers()
        if self.cfg.provider == "dml" and "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
        if self.cfg.provider == "dml":
            log.warning("DirectML richiesto ma non disponibile: uso la CPU")
        return ["CPUExecutionProvider"]

    def session(self, path: Path) -> ort.InferenceSession:
        path = Path(path)
        with self._lock:
            sess = self._sessions.get(path)
            if sess is None:
                if not path.exists():
                    raise FileNotFoundError(f"modello non trovato: {path}")
                options = ort.SessionOptions()
                options.intra_op_num_threads = self.cfg.threads or max(1, (os.cpu_count() or 2) // 2)
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                # con 8 GB di RAM l'arena e i pattern di memoria fanno solo crescere il consumo di picco
                options.log_severity_level = 3  # ArcFace dichiara batch 1 nei metadati ma accetta N: niente avvisi a ogni foto
                options.enable_cpu_mem_arena = False
                options.enable_mem_pattern = False
                if self.providers[0] == "DmlExecutionProvider":
                    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess = ort.InferenceSession(str(path), options, providers=self.providers)
                self._sessions[path] = sess
            return sess

    def unload(self, path: Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._sessions.clear()
            else:
                self._sessions.pop(Path(path), None)

    def close(self) -> None:
        self.unload()
