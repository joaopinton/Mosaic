"""Auto-indicizzazione: osserva le cartelle della libreria e, dopo un periodo di quiete, avvia un'indicizzazione."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mosaic.index.discover import KNOWN_EXT

log = logging.getLogger(__name__)


class LibraryWatcher(FileSystemEventHandler):
    """Raggruppa le modifiche ravvicinate (debounce): un solo `trigger` dopo `delay` secondi senza eventi."""

    def __init__(self, roots: list[str], delay: float, trigger: Callable[[], None], ignore: list[Path] | None = None) -> None:
        self.roots = [Path(r) for r in roots]
        self.delay = delay
        self.trigger = trigger
        self.ignore = [Path(p).resolve() for p in (ignore or [])]
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._observer: Observer | None = None

    def start(self) -> None:
        if self.delay <= 0 or self._observer is not None:
            return
        observer = Observer()
        for root in self.roots:
            if root.is_dir():
                observer.schedule(self, str(root), recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def _relevant(self, event: FileSystemEvent) -> bool:
        if event.is_directory:
            return False
        paths = [str(event.src_path), str(getattr(event, "dest_path", ""))]
        for raw in paths:
            if not raw:
                continue
            path = Path(raw)
            name = path.name
            if name.startswith(".") or name.endswith(".part") or name.endswith(".tmp"):
                continue
            if any(parent in self.ignore or path.resolve() == parent for parent in self.ignore):
                continue
            if path.suffix.lower() in KNOWN_EXT:
                return True
        return False

    def notify(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self.trigger()
        except Exception:  # un errore nel trigger non deve fermare l'osservatore
            log.exception("auto-indicizzazione non riuscita")

    # watchdog
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("created", "modified", "moved", "deleted") and self._relevant(event):
            self.notify()
