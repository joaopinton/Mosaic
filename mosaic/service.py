"""Servizio applicativo: contesto, coda di job, bus di eventi e osservatore della libreria in un unico oggetto."""

from __future__ import annotations

import logging
from pathlib import Path

from mosaic import config as config_mod
from mosaic.ai.labels import prune_for_context
from mosaic.config import Config
from mosaic.context import Context
from mosaic.jobs.events import EventBus
from mosaic.jobs.handlers import build_handlers
from mosaic.jobs.worker import JobRunner
from mosaic.organize.watcher import LibraryWatcher

log = logging.getLogger(__name__)


class Mosaic:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.bus = EventBus()
        self.runner = JobRunner(ctx, build_handlers(), self.bus)
        self.watcher: LibraryWatcher | None = None

    @classmethod
    def open(cls, cfg: Config | None = None, models_dir: Path | None = None) -> "Mosaic":
        return cls(Context.open(cfg or config_mod.load(), models_dir))

    @property
    def cfg(self) -> Config:
        return self.ctx.cfg

    def start(self) -> None:
        self._prune_labels()
        self._prune_background_faces()
        self.runner.start()
        self._start_watcher()

    def _prune_labels(self) -> None:
        try:
            prune_for_context(self.ctx)
        except Exception:  # la pulizia non deve impedire l'avvio
            log.exception("pulizia delle etichette non riuscita")

    def _prune_background_faces(self) -> None:
        """Le librerie analizzate prima di D-22 perdono i volti della folla; i gruppi e i conteggi si rifanno subito."""
        from mosaic.ai.face_worker import prune_background
        from mosaic.ai.registry import face_spec

        try:
            with self.ctx.db.tx() as conn:
                removed = prune_background(conn, face_spec(self.ctx.models_dir))
            if removed:
                log.info("volti sullo sfondo tolti: %d", removed)
                self.runner.submit("faces", {}, unique=True)
        except Exception:  # la pulizia non deve impedire l'avvio
            log.exception("pulizia dei volti sullo sfondo non riuscita")

    def _start_watcher(self) -> None:
        delay = self.cfg.auto_index_delay
        if delay > 0 and self.cfg.library_roots and self.watcher is None:
            self.watcher = LibraryWatcher(
                list(self.cfg.library_roots), delay, lambda: self.runner.submit("index", {}, unique=True),
                ignore=[self.cfg.data_path])
            self.watcher.start()

    def reload_config(self, cfg: Config) -> None:
        """Applica una nuova configurazione (salvata su disco) senza riavviare."""
        config_mod.save(cfg)
        self.ctx.cfg = cfg
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        self._start_watcher()

    def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.runner.stop()
        self.ctx.close()
