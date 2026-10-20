"""Avvio di Mosaic: `python -m mosaic.app` (usato da Mosaic.bat). Un solo processo su 127.0.0.1."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from mosaic import config as config_mod
from mosaic.api.app import create_app
from mosaic.paths import ROOT
from mosaic.service import Mosaic

HOST = "127.0.0.1"
LOG_FILE = ROOT / "data" / "mosaic.log"


def is_running(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")] + (
                            [logging.StreamHandler()] if sys.stderr is not None else []))


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(100):
        if is_running(port):
            webbrowser.open(url)
            return
        time.sleep(0.2)


def serve(port: int | None = None, open_browser: bool = True) -> int:
    cfg = config_mod.load()
    port = port or cfg.port
    url = f"http://{HOST}:{port}/"
    if is_running(port):  # già avviato: si apre solo il browser (come launch_mosaic.pyw)
        if open_browser:
            webbrowser.open(url)
        return 0
    _setup_logging()
    mosaic = Mosaic.open(cfg)
    app = create_app(mosaic)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning", log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
