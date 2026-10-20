"""Bus di eventi in memoria: i worker pubblicano, l'API (SSE) e la CLI ascoltano."""

from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    """Pubblica/sottoscrivi thread-safe. Ogni ascoltatore ha una coda limitata: se è lenta perde gli eventi vecchi."""

    def __init__(self, maxsize: int = 256) -> None:
        self._lock = threading.Lock()
        self._listeners: list[queue.Queue[dict[str, Any]]] = []
        self._maxsize = maxsize

    def subscribe(self) -> "queue.Queue[dict[str, Any]]":
        q: queue.Queue[dict[str, Any]] = queue.Queue(self._maxsize)
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue[dict[str, Any]]") -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass
