"""llama-server (llama.cpp) gestito da Mosaic: avvio su richiesta solo su 127.0.0.1, spegnimento per inattività."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

import httpx

from mosaic.ai.registry import LlmSpec, llm_spec
from mosaic.context import Context
from mosaic.paths import ROOT

log = logging.getLogger(__name__)

START_TIMEOUT = 240.0
NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

CommandBuilder = Callable[[int], list[str]]
LOG_FILE = "llama-server.log"


def default_threads() -> int:
    """Core fisici (stimati come metà dei thread logici): su CPU llama.cpp rallenta con l'hyper-threading."""
    return max(1, (os.cpu_count() or 2) // 2)


class LlmUnavailable(RuntimeError):
    """L'LLM locale non è installato o non parte."""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    def __init__(self, ctx: Context, command: CommandBuilder | None = None, spec: LlmSpec | None = None) -> None:
        self.ctx = ctx
        self.spec = spec or llm_spec(ctx.models_dir, ctx.cfg.llm.model)
        self._command = command
        self._proc: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self._port = 0
        self._lock = threading.RLock()
        self._last_used = time.monotonic()
        self._busy = 0
        self._watch: threading.Thread | None = None
        self._stop_watch = threading.Event()

    # --- percorsi -----------------------------------------------------------------------
    @property
    def model_path(self) -> Path:
        return self.ctx.models_dir / self.spec.dir / self.spec.model

    @property
    def mmproj_path(self) -> Path:
        return self.ctx.models_dir / self.spec.dir / self.spec.mmproj

    @property
    def log_path(self) -> Path:
        return self.ctx.data_dir / LOG_FILE

    @property
    def exe(self) -> Path | None:
        folder = ROOT / self.spec.server
        found = next(folder.rglob("llama-server.exe"), None) if folder.exists() else None
        return found

    @property
    def available(self) -> bool:
        if self._command is not None:
            return True
        return bool(self.exe and self.model_path.exists() and self.mmproj_path.exists())

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def _build_command(self, port: int) -> list[str]:
        if self._command is not None:
            return self._command(port)
        cfg = self.ctx.cfg.llm
        threads = str(cfg.threads or default_threads())
        # uno slot per ogni richiesta in parallelo: il contesto si divide tra gli slot, così ognuno ha `context` token
        return [str(self.exe), "-m", str(self.model_path), "--mmproj", str(self.mmproj_path), "--host", "127.0.0.1",
                "--port", str(port), "-c", str(cfg.context * cfg.parallel), "-np", str(cfg.parallel),
                "-t", threads, "-tb", threads]

    # --- ciclo di vita ------------------------------------------------------------------
    def ensure(self) -> str:
        """Avvia il server se serve e ritorna l'URL di base. Solleva LlmUnavailable se non parte."""
        with self._lock:
            self._last_used = time.monotonic()
            if self.running:
                return self.base_url
            if not self.available:
                raise LlmUnavailable("LLM locale non installato: esegui `python -m mosaic.assets fetch --group llm`")
            self._port = free_port()
            log.info("avvio llama-server sulla porta %s (log in %s)", self._port, self.log_path)
            self._close_log()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("wb")  # sovrascritto a ogni avvio: contiene il motivo dell'ultima chiusura
            self._proc = subprocess.Popen(
                self._build_command(self._port), stdout=self._log, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW)
            self._wait_ready()
            self._start_watch()
            return self.base_url

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = None
                raise LlmUnavailable(f"llama-server si è chiuso durante l'avvio: vedi {self.log_path}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self.stop()
        raise LlmUnavailable("llama-server non è pronto dopo il tempo massimo di avvio")

    def touch(self) -> None:
        self._last_used = time.monotonic()

    def busy(self) -> "_Busy":
        """Context manager: finché è attivo il server non viene spento per inattività."""
        return _Busy(self)

    def restart(self) -> str:
        """Ferma il processo (anche se è già morto o bloccato) e ne avvia uno nuovo."""
        with self._lock:
            self.stop()
            return self.ensure()

    def stop(self) -> None:
        with self._lock:
            self._stop_watch.set()
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            log.info("llama-server fermato")
        self._close_log()

    def _close_log(self) -> None:
        with self._lock:
            handle, self._log = self._log, None
        if handle is not None:
            handle.close()

    close = stop

    def _start_watch(self) -> None:
        self._stop_watch.clear()
        if self._watch is None or not self._watch.is_alive():
            self._watch = threading.Thread(target=self._idle_loop, name="llm-idle", daemon=True)
            self._watch.start()

    def _idle_loop(self) -> None:
        idle = self.ctx.cfg.llm.idle_seconds
        while not self._stop_watch.wait(min(5.0, max(0.05, idle / 4))):
            with self._lock:
                expired = self._busy == 0 and time.monotonic() - self._last_used > idle
            if expired:
                self.stop()
                return


class _Busy:
    def __init__(self, server: LlamaServer) -> None:
        self.server = server

    def __enter__(self) -> LlamaServer:
        with self.server._lock:
            self.server._busy += 1
        return self.server

    def __exit__(self, *exc: object) -> None:
        with self.server._lock:
            self.server._busy -= 1
            self.server._last_used = time.monotonic()


def server_for(ctx: Context) -> LlamaServer:
    return ctx.service("llm", lambda: LlamaServer(ctx))


def stop_llm(ctx: Context) -> None:
    """Spegne l'LLM se è attivo (prima di caricare SigLIP, per non esaurire la RAM)."""
    server = ctx._extras.get("llm")
    if server is not None:
        server.stop()
