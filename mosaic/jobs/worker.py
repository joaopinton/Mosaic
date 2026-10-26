"""Esecuzione dei job: pool di thread, esclusione reciproca dei job pesanti, annullamento e progresso."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from mosaic.context import Context
from mosaic.jobs.events import EventBus
from mosaic.jobs.queue import CANCELLED, DONE, FAILED, Job, JobQueue

log = logging.getLogger(__name__)

# Job leggeri: possono girare in parallelo. Tutti gli altri si escludono a vicenda
# (con 8 GB di RAM, per esempio, SigLIP e l'LLM non devono mai convivere).
# Il poster e il backup YAML (parte dopo ogni modifica manuale) non devono aspettare la fine di un'analisi lunga.
PARALLEL_TYPES = {"process", "poster", "backup", "export"}
INTERACTIVE_TYPES = {"export", "poster"}   # lanciati da un clic: hanno un worker tutto per loro, mai dietro un'analisi lunga
MAX_ATTEMPTS = 2


class JobControl:
    """Passato all'handler: permette di segnalare il progresso e di controllare l'annullamento."""

    def __init__(self, queue: JobQueue, job: Job, bus: EventBus, cancel: threading.Event,
                 submit: Callable[..., int] | None = None) -> None:
        self._queue = queue
        self.job = job
        self._bus = bus
        self.cancel = cancel
        self._submit = submit

    def submit(self, job_type: str, payload: dict[str, Any] | None = None, unique: bool = False) -> int:
        """Accoda un altro job (per concatenare le fasi della pipeline)."""
        if self._submit is None:
            raise RuntimeError("questo controllo non può accodare job")
        return self._submit(job_type, payload, unique)

    def progress(self, value: float, message: str = "", done: int | None = None, total: int | None = None) -> None:
        """Avanzamento del job. `done` e `total` (elementi fatti su quanti) danno all'interfaccia barre e tempo rimanente."""
        self._queue.progress(self.job.id, value, message, done, total)
        event: dict[str, Any] = {"event": "job", "id": self.job.id, "type": self.job.type, "state": "running",
                                 "progress": value, "message": message}
        if done is not None:
            event["done"] = done
        if total is not None:
            event["total"] = total
        self._bus.publish(event)


Handler = Callable[[Context, Job, JobControl], Any]


class JobRunner:
    def __init__(self, ctx: Context, handlers: dict[str, Handler], bus: EventBus | None = None,
                 workers: int | None = None) -> None:
        self.ctx = ctx
        self.queue = JobQueue(ctx.db)
        self.handlers = handlers
        self.bus = bus or EventBus()
        self.workers = workers or ctx.cfg.worker_count()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Condition()
        self._lock = threading.Lock()
        self._running_heavy = 0
        self._cancels: dict[int, threading.Event] = {}

    # --- ciclo di vita ------------------------------------------------------------
    def start(self) -> None:
        self.queue.reset_running()
        self._stop.clear()
        for i in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"mosaic-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        lane = threading.Thread(target=self._loop, args=(INTERACTIVE_TYPES & set(self.handlers),), name="mosaic-worker-interactive", daemon=True)
        lane.start()
        self._threads.append(lane)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for ev in list(self._cancels.values()):
            ev.set()
        self.wake()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()

    def wake(self) -> None:
        with self._wake:
            self._wake.notify_all()

    def submit(self, job_type: str, payload: dict[str, Any] | None = None, unique: bool = False) -> int:
        if job_type not in self.handlers:
            raise ValueError(f"tipo di job sconosciuto: {job_type}")
        job_id = self.queue.enqueue(job_type, payload, unique=unique)
        ids = (payload or {}).get("ids")
        total = len(ids) if isinstance(ids, list) else 0  # i blocchi in coda contano già nel totale della fase
        self.bus.publish({"event": "job", "id": job_id, "type": job_type, "state": "queued", "progress": 0, "done": 0, "total": total})
        self.wake()
        return job_id

    def cancel(self, job_id: int) -> bool:
        ev = self._cancels.get(job_id)
        if ev is not None:
            ev.set()
            return True
        return self.queue.cancel(job_id)

    def cancel_type(self, job_type: str) -> int:
        """Annulla tutti i job di un tipo, in coda e in corso (una fase intera dal pannello Attività)."""
        rows = self.db_active(job_type)
        cancelled = 0
        for row in rows:
            running = row["state"] == "running"
            if self.cancel(row["id"]):
                cancelled += 1
                # subito un segno nell'interfaccia: quello in corso si ferma al primo controllo (di solito meno di 2 s)
                self.bus.publish({"event": "job", "id": row["id"], "type": job_type,
                                  "state": "running" if running else "cancelled", **({"message": "In arresto…"} if running else {})})
        return cancelled

    RESTART = {"index": ("index", {}), "process": ("index", {}), "analyze": ("analyze", {}), "faces": ("faces", {}),
               "moments": ("moments", {}), "caption": ("caption", {}), "rename": ("rename", {})}

    def restart_type(self, job_type: str) -> int:
        """Ferma una fase e la rimette in coda da capo. I job pesanti non si sovrappongono: il nuovo parte appena
        il vecchio si è fermato. I blocchi di metadati si ricreano rilanciando l'indicizzazione."""
        if job_type not in self.RESTART:
            raise ValueError(f"questa attività non si può riavviare: {job_type}")
        self.cancel_type(job_type)
        new_type, payload = self.RESTART[job_type]
        return self.submit(new_type, payload, unique=True)

    def db_active(self, job_type: str) -> list:
        return self.ctx.db.all("SELECT id, state FROM jobs WHERE type=? AND state IN ('queued','running')", (job_type,))

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """Attende che la coda si svuoti (usato dalla CLI e dai test)."""
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.queue.pending() == 0:
                return True
            time.sleep(0.05)
        return False

    # --- interno ------------------------------------------------------------------
    def _next_job(self, allowed: set[str] | None = None) -> Job | None:
        with self._lock:
            excluded = None if self._running_heavy == 0 else {t for t in self.handlers if t not in PARALLEL_TYPES}
            job = self.queue.claim(allowed if allowed is not None else set(self.handlers), excluded)
            if job is not None and job.type not in PARALLEL_TYPES:
                self._running_heavy += 1
            return job

    def _loop(self, allowed: set[str] | None = None) -> None:
        while not self._stop.is_set():
            job = self._next_job(allowed)
            if job is None:
                with self._wake:
                    self._wake.wait(timeout=0.5)
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        cancel = threading.Event()
        self._cancels[job.id] = cancel
        control = JobControl(self.queue, job, self.bus, cancel, self.submit)
        state, error = DONE, ""
        try:
            self.handlers[job.type](self.ctx, job, control)
            if cancel.is_set():
                state = CANCELLED
        except Exception as exc:  # il job fallisce, il worker continua
            log.exception("job %s (%s) fallito", job.id, job.type)
            error = f"{type(exc).__name__}: {exc}"
            self.ctx.db.log_error(f"job:{job.type}", str(job.id), error)
            state = FAILED
        finally:
            self._cancels.pop(job.id, None)
            if job.type not in PARALLEL_TYPES:
                with self._lock:
                    self._running_heavy -= 1
        if state == FAILED and job.attempts < MAX_ATTEMPTS:
            self.queue.requeue(job.id, error)
            self.bus.publish({"event": "job", "id": job.id, "type": job.type, "state": "queued", "error": error})
        else:
            self.queue.finish(job.id, state, error)
            self.bus.publish({"event": "job", "id": job.id, "type": job.type, "state": state,
                              "progress": 1 if state == DONE else 0, "error": error})
        self.wake()
