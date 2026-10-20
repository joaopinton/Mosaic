"""Didascalie in background: titolo, parole chiave e, se `llm.caption`, la frase di didascalia dall'LLM (cloud se attivato,
altrimenti locale), con fonte `llm` (non sovrascrive XMP né manuale)."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import deque
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass, field

import httpx

from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.llm import captions, context
from mosaic.llm.backends import Backend, LocalBackend, Request, build_chain
from mosaic.llm.captions import BackendDown, LlmDied, LlmError, RateLimited
from mosaic.llm.server import LlamaServer, LlmUnavailable
from mosaic.meta import sources
from mosaic.organize import tags
from mosaic.pipeline import thumb_source
from mosaic.search import fts

log = logging.getLogger(__name__)

MAX_RESTARTS_IN_A_ROW = 1
MIN_WAIT = 0.05  # secondi: pausa minima quando si aspetta che un backend torni disponibile
CANCEL_POLL = 0.25  # secondi: ogni quanto, con richieste in volo, si guarda se l'utente ha annullato

DONE, RETRY, DIED = "done", "retry", "died"  # esito di una richiesta: registrata / da rifare altrove / server locale morto


@dataclass
class CaptionStats:
    photos: int = 0
    failed: int = 0
    skipped: int = 0
    restarts: int = 0
    by_backend: dict[str, int] = field(default_factory=dict)  # foto descritte da ciascun backend

    def where(self) -> str:
        """" (nvidia 40, locale 3)" quando è intervenuto il cloud; vuoto se ha fatto tutto il modello locale."""
        if all(name == LocalBackend.name for name in self.by_backend):
            return ""
        return " (" + ", ".join(f"{name} {count}" for name, count in self.by_backend.items()) + ")"


def pending_caption_ids(ctx: Context, force: bool = False, limit: int | None = None) -> list[int]:
    """Foto senza didascalia LLM, escluse quelle che hanno già titolo e didascalia da XMP o manuali."""
    sql = "SELECT id FROM photos WHERE deleted_at IS NULL AND meta_at IS NOT NULL"
    if not force:
        sql += f" AND captioned_at IS NULL AND NOT (title_src>={sources.XMP} AND caption_src>={sources.XMP})"
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["id"] for r in ctx.db.all(sql)]


def people_in_photo(conn: sqlite3.Connection, photo_id: int) -> list[str]:
    """Nomi delle persone confermate nella foto, da sinistra a destra. I suggerimenti non contano."""
    rows = conn.execute(
        "SELECT s.name FROM markers m JOIN subjects s ON s.id=m.subj_id "
        "WHERE m.photo_id=? AND m.invalid=0 ORDER BY m.x", (photo_id,)).fetchall()
    names: list[str] = []
    for row in rows:
        if row["name"] not in names:
            names.append(row["name"])
    return names


def format_eta(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    return f"~{hours} h {rest} min" if hours else f"~{minutes} min"


def photo_context(ctx: Context, stories: Sequence[context.Story], photo_id: int) -> str:
    """Contesto di una foto: chi è l'utente, le giornate degli album che la contengono, quando è stata scattata."""
    taken = ctx.db.scalar("SELECT taken_at_local FROM photos WHERE id=?", (photo_id,))
    language = ctx.cfg.llm.language
    return context.compose(ctx.cfg.llm.about, context.stories_for(stories, photo_id),
                           context.describe_moment(taken, language), language)


class _Run:
    """Un giro di didascalie su una catena di backend: cloud nell'ordine scelto, poi il modello locale.

    Ogni backend ha i suoi slot (`parallel`) e il suo ritmo (richieste al minuto). Una foto che il cloud non può
    servire (troppe richieste, chiave rifiutata, servizio giù) non è fallita: torna in coda e va al backend successivo.
    Il modello locale lavora solo quando nessun servizio cloud è utilizzabile. Su CPU generare più risposte insieme
    costa poco più di una; il database si tocca solo da questo thread, nel pool gira soltanto la richiesta HTTP.
    """

    def __init__(self, ctx: Context, chain: Sequence[Backend], control, total: int,  # type: ignore[no-untyped-def]
                 stack: ExitStack) -> None:
        self.ctx = ctx
        self.chain = list(chain)
        self.local = next((b for b in self.chain if isinstance(b, LocalBackend)), None)
        self.control = control
        self.total = total
        self.stack = stack
        self.stats = CaptionStats()
        self.stories = context.load_stories(ctx)
        self.started = time.monotonic()
        self.done = 0
        self.deaths = 0  # chiusure del server di fila, senza una risposta riuscita in mezzo
        self.last_death: LlmDied | None = None

    def cancelled(self) -> bool:
        return self.control is not None and self.control.cancel.is_set()

    # --- scelta del backend ---------------------------------------------------------------------------
    def candidates(self, now: float) -> list[Backend]:
        """Backend che possono lavorare adesso o a breve: il locale solo se nessun servizio cloud è utilizzabile."""
        cloud_alive = any(b.is_cloud and b.alive(now) for b in self.chain)
        return [b for b in self.chain if not b.dead and (b.is_cloud or not cloud_alive)]

    def pick(self, now: float) -> Backend | None:
        return next((b for b in self.candidates(now) if b.ready(now)), None)

    def wake_delay(self, now: float) -> float | None:
        """Tra quanto un backend con uno slot libero tocca di nuovo a noi (ritmo o fine del riposo); None se nessuno."""
        waits = [max(b.next_at, b.cooldown_until) - now for b in self.candidates(now) if b.in_flight < b.parallel]
        return max(MIN_WAIT, min(waits)) if waits else None

    def wait_for_backend(self) -> None:
        """Nessuna richiesta in volo e nessun backend pronto: si aspetta il ritmo o il riposo più vicino."""
        now = time.monotonic()
        if not self.candidates(now):
            refused = [b.label for b in self.chain if b.dead]
            raise LlmUnavailable(
                "nessun modello utilizzabile per le didascalie"
                + (f" ({', '.join(refused)} ha rifiutato la richiesta: controlla chiave e modello in Impostazioni)" if refused else ""))
        delay = self.wake_delay(now)
        self.sleep(delay if delay is not None else MIN_WAIT)

    def sleep(self, seconds: float) -> None:
        if self.control is not None:
            self.control.cancel.wait(seconds)  # si sveglia subito se l'utente annulla
        else:
            time.sleep(seconds)

    # --- ciclo principale -----------------------------------------------------------------------------
    def run(self, photo_ids: Sequence[int], client: httpx.Client, pool: ThreadPoolExecutor) -> None:
        queue = deque(photo_ids)
        in_flight: dict[Future[captions.Caption], tuple[int, Backend]] = {}
        self.started = time.monotonic()
        while True:
            if self.cancelled():  # le richieste in volo si abbandonano: aspettarle costerebbe fino a mezzo minuto l'una
                for _, backend in in_flight.values():
                    backend.release()
                in_flight.clear()
                return
            self.dispatch(queue, in_flight, client, pool)
            if not in_flight:
                if not queue or self.cancelled():
                    return
                self.wait_for_backend()
                continue
            delay = self.wake_delay(time.monotonic()) if queue else None
            timeout = CANCEL_POLL if delay is None else min(delay, CANCEL_POLL)  # ogni 0,25 s si guarda se annullare
            finished, _ = wait(in_flight, timeout=timeout, return_when=FIRST_COMPLETED)
            retry, died = self.collect_all(in_flight, finished)
            if died:  # le altre richieste in volo andavano allo stesso server: si aspettano prima di riavviarlo
                local_futures = [f for f, (_, b) in in_flight.items() if b is self.local]
                more_retry, more_died = self.collect_all(in_flight, local_futures)
                retry, died = retry + more_retry, died + more_died
            queue.extendleft(reversed(retry + died))
            if died and not self.cancelled():  # annullato: nessuna foto nuova partirebbe comunque
                self.restart()

    def dispatch(self, queue: deque[int], in_flight: dict[Future[captions.Caption], tuple[int, Backend]],
                 client: httpx.Client, pool: ThreadPoolExecutor) -> None:
        while queue and not self.cancelled():
            backend = self.pick(time.monotonic())
            if backend is None:
                return
            pid = queue.popleft()
            request = self.prepare(pid)
            if request is None:
                continue
            backend.start(self.stack)
            backend.reserve(time.monotonic())
            in_flight[pool.submit(backend.describe, request, self.ctx.cfg.llm.language, client)] = (pid, backend)

    def collect_all(self, in_flight: dict[Future[captions.Caption], tuple[int, Backend]],
                    futures) -> tuple[list[int], list[int]]:  # type: ignore[no-untyped-def]
        """Registra le risposte di `futures` (attendendole). Ritorna le foto da rifare e quelle perse per il server morto."""
        retry: list[int] = []
        died: list[int] = []
        for fut in futures:
            pid, backend = in_flight.pop(fut)
            backend.release()
            outcome = self.collect(pid, backend, fut)
            if outcome == RETRY:
                retry.append(pid)
            elif outcome == DIED:
                died.append(pid)
        return retry, died

    # --- una foto -------------------------------------------------------------------------------------
    def prepare(self, pid: int) -> Request | None:
        path = thumb_source(self.ctx, pid, "fit_720")
        if path is None:
            self.stats.skipped += 1
            self.advance()
            return None
        try:
            image = captions.prepare_image(path, self.ctx.cfg.llm.image_size)
        except OSError as exc:
            self.stats.failed += 1
            self.ctx.db.log_error("caption", str(pid), f"miniatura illeggibile: {exc}")
            self.advance()
            return None
        return Request(image, people_in_photo(self.ctx.db.conn(), pid), photo_context(self.ctx, self.stories, pid),
                       self.ctx.cfg.llm.caption)

    def collect(self, pid: int, backend: Backend, fut: Future[captions.Caption]) -> str:
        """Registra la risposta di una foto. RETRY/DIED: la foto va rifatta, non è fallita."""
        now = time.monotonic()
        try:
            result = fut.result()
        except LlmDied as exc:
            self.last_death = exc
            return DIED
        except RateLimited as exc:
            pause = backend.rate_limited(now, exc.retry_after)
            log.warning("%s: troppe richieste, riposo di %.0f s", backend.label, pause)
            return RETRY
        except BackendDown as exc:
            self.backend_down(backend, exc, now)
            return RETRY
        except LlmError as exc:
            self.stats.failed += 1
            self.ctx.db.log_error("caption", str(pid), str(exc))
        else:
            self.save(pid, result)
            backend.succeeded()
            self.deaths = 0
            self.stats.photos += 1
            self.stats.by_backend[backend.name] = self.stats.by_backend.get(backend.name, 0) + 1
            if isinstance(backend, LocalBackend):
                backend.server.touch()
            if result.timings is not None:
                log.info("didascalia della foto %s: %s", pid, result.timings.summary())
            else:  # il cloud non manda i tempi: si misura la richiesta intera (rete, coda del servizio e risposta)
                log.info("didascalia della foto %s da %s in %.1f s", pid, backend.label, result.elapsed)
        self.advance()
        return DONE

    def backend_down(self, backend: Backend, error: BackendDown, now: float) -> None:
        reason = f"HTTP {error.status}" if error.status else str(error)
        pause = backend.failed(now, error)
        if pause is None:
            message = f"{backend.label} ha rifiutato la richiesta ({reason}): controlla chiave e modello in Impostazioni"
            self.ctx.db.log_error("caption", backend.name, message)
            log.warning(message)
        else:
            log.warning("%s non risponde (%s), riposo di %.0f s", backend.label, reason, pause)

    def save(self, pid: int, result: captions.Caption) -> None:
        with self.ctx.db.tx() as conn:
            sources.set_text(conn, pid, "title", result.title, sources.LLM)
            if self.ctx.cfg.llm.caption:  # senza, l'LLM non la scrive: resta quella che c'era (XMP, manuale o vuota)
                sources.set_text(conn, pid, "caption", result.caption, sources.LLM)
            tags.add_keywords(conn, pid, result.keywords, sources.LLM)
            conn.execute("UPDATE photos SET captioned_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), pid))
            fts.reindex(conn, pid)

    def advance(self) -> None:
        self.done += 1
        if self.control is None:
            return
        remaining = self.total - self.done
        # con più richieste in volo conta il ritmo reale (foto finite nel tempo trascorso), non la durata di una foto
        eta = f" · {format_eta((time.monotonic() - self.started) / self.done * remaining)}" if remaining > 0 else ""
        self.control.progress(self.done / max(self.total, 1), f"{self.done}/{self.total} didascalie{eta}", self.done, self.total)

    def restart(self) -> None:
        assert self.local is not None  # DIED arriva solo dal backend locale
        if self.deaths >= MAX_RESTARTS_IN_A_ROW:
            raise LlmUnavailable(
                f"llama-server si chiude a ogni richiesta ({self.last_death}): vedi {self.local.server.log_path}") from self.last_death
        log.warning("llama-server non risponde, lo riavvio: %s", self.last_death)
        self.local.server.restart()
        self.deaths += 1
        self.stats.restarts += 1


def caption_photos(ctx: Context, photo_ids: Sequence[int], control=None,  # type: ignore[no-untyped-def]
                   server: LlamaServer | None = None, chain: Sequence[Backend] | None = None,
                   client: httpx.Client | None = None) -> CaptionStats:
    """Descrive le foto. `chain` e `client` servono ai test; di norma la catena viene da `build_chain` (config e chiavi)."""
    backends = list(chain) if chain is not None else build_chain(ctx, server)
    if not backends:
        raise LlmUnavailable("LLM locale non installato (esegui `python -m mosaic.assets fetch --group llm`) "
                             "e nessun servizio cloud attivo con una chiave")
    with ExitStack() as stack:
        run = _Run(ctx, backends, control, len(photo_ids), stack)
        http = client or httpx.Client(timeout=captions.REQUEST_TIMEOUT)
        pool = ThreadPoolExecutor(max_workers=sum(b.parallel for b in backends), thread_name_prefix="llm")
        try:
            run.run(photo_ids, http, pool)
        finally:
            cancelled = run.cancelled()
            if client is None and cancelled:
                http.close()  # interrompe le richieste in volo: llama-server smette di generare per una connessione chiusa
            pool.shutdown(wait=not cancelled, cancel_futures=cancelled)
            if client is None and not cancelled:
                http.close()
    return run.stats
