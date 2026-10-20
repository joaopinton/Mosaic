"""Mosaic come app desktop: `python -m mosaic.desktop` (usato da Mosaic.exe).

Il server locale gira in un thread dello stesso processo e l'interfaccia si apre in una finestra nativa
(pywebview con Edge WebView2). Chiudendo la finestra si ferma anche il server. Se WebView2 non è disponibile
si ripiega su Edge in modalità app (finestra senza barra del browser) e, come ultima scelta, sul browser.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from mosaic import config as config_mod
from mosaic.app import HOST, LOG_FILE, _setup_logging, is_running
from mosaic.paths import ROOT

log = logging.getLogger("mosaic.desktop")

TITLE = "Mosaic"
APP_ID = "Mosaic.Desktop.2"
WINDOW_SIZE = (1440, 920)
MIN_SIZE = (960, 640)
READY_TIMEOUT_S = 60.0
SHUTDOWN_TIMEOUT_S = 10.0
ICON = ROOT / "mosaic.ico"
WEBVIEW_DIR = ROOT / "data" / "webview"


def edge_executable() -> Path | None:
    """Percorso di msedge.exe (presente su ogni Windows 10/11 aggiornato), oppure None."""
    roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for root in filter(None, roots):
        candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if candidate.is_file():
            return candidate
    found = shutil.which("msedge")
    return Path(found) if found else None


def edge_app_args(edge: Path, url: str, profile_dir: Path) -> list[str]:
    """Edge in modalità app con un profilo dedicato: il processo resta vivo finché la finestra è aperta."""
    w, h = WINDOW_SIZE
    return [str(edge), f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={w},{h}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]


def wait_until_ready(port: int, timeout: float = READY_TIMEOUT_S, poll: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running(port):
            return True
        time.sleep(poll)
    return False


def _set_app_id() -> None:
    """Icona e raggruppamento propri nella barra delle applicazioni (invece di quelli di pythonw)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        log.debug("AppUserModelID non impostato", exc_info=True)


def _apply_window_icon(window: object) -> None:
    """pywebview su Windows usa l'icona di pythonw: imposto quella di Mosaic sul Form WinForms sottostante."""
    if not ICON.is_file():
        return
    try:
        from System import Action  # type: ignore[import-not-found]  # via pythonnet
        from System.Drawing import Icon  # type: ignore[import-not-found]

        window.events.shown.wait(15)  # type: ignore[attr-defined]
        form = window.native  # type: ignore[attr-defined]
        icon = Icon(str(ICON))
        form.Invoke(Action(lambda: setattr(form, "Icon", icon)))
    except Exception:  # l'icona è solo estetica: un errore qui non deve fermare l'app
        log.debug("icona della finestra non impostata", exc_info=True)


def dropped_paths(event: dict) -> list[str]:
    """Percorsi veri dei file rilasciati sulla finestra (pywebview li aggiunge come `pywebviewFullPath`)."""
    files = (event.get("dataTransfer") or {}).get("files") or []
    return [f["pywebviewFullPath"] for f in files if isinstance(f, dict) and f.get("pywebviewFullPath")]


def drop_script(paths: list[str]) -> str:
    """JavaScript che consegna i percorsi all'interfaccia (D-25); JSON evita ogni problema di virgolette nei nomi."""
    return f"window.mosaicDrop && window.mosaicDrop({json.dumps(paths, ensure_ascii=False)})"


def _bind_drop(window: object) -> None:
    """Il trascina e rilascia con i percorsi veri: senza, l'interfaccia ripiega sul caricamento dei file."""
    try:
        from webview.dom import DOMEventHandler

        def on_drop(event: dict) -> None:
            paths = dropped_paths(event)
            if paths:
                window.evaluate_js(drop_script(paths))  # type: ignore[attr-defined]

        window.events.loaded.wait(30)  # type: ignore[attr-defined]
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)  # type: ignore[attr-defined]
    except Exception:  # senza i percorsi l'app funziona lo stesso (caricamento dal browser)
        log.warning("trascina e rilascia con i percorsi non disponibile", exc_info=True)


def _on_start(window: object) -> None:
    threading.Thread(target=_bind_drop, args=(window,), name="mosaic-drop", daemon=True).start()
    _apply_window_icon(window)


_window: object | None = None  # la finestra WebView2 aperta: serve a «Scegli cartella» (D-30)


def pick_folder(start: str = "") -> str | None:
    """Apre la finestra di Windows per scegliere una cartella. RuntimeError fuori dall'app desktop."""
    if _window is None:
        raise RuntimeError("finestra di scelta disponibile solo nell'app desktop")
    import webview

    folder = Path(start) if start else Path.home()
    while not folder.is_dir() and folder.parent != folder:  # la cartella proposta di solito non esiste ancora
        folder = folder.parent
    chosen = _window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(folder))  # type: ignore[attr-defined]
    return str(chosen[0]) if chosen else None


def open_webview(url: str) -> bool:
    """Finestra nativa con WebView2. Ritorna False se pywebview/WebView2 non sono utilizzabili."""
    global _window
    try:
        import webview
    except ImportError:
        log.warning("pywebview non installato: uso Edge in modalità app")
        return False
    try:
        WEBVIEW_DIR.mkdir(parents=True, exist_ok=True)
        webview.settings["ALLOW_DOWNLOADS"] = True  # «Scarica» del poster: senza, WebView2 ignora il clic
        window = webview.create_window(TITLE, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                                       min_size=MIN_SIZE, text_select=True, zoomable=True)
        _window = window
        webview.start(_on_start, window, gui="edgechromium", private_mode=False,
                      storage_path=str(WEBVIEW_DIR))
        return True
    except Exception:
        log.exception("finestra WebView2 non disponibile: uso Edge in modalità app")
        return False


def open_edge_app(url: str) -> bool:
    edge = edge_executable()
    if edge is None:
        return False
    profile = ROOT / "data" / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(edge_app_args(edge, url, profile), check=False)
    except OSError:
        log.exception("avvio di Edge fallito")
        return False
    return True


def show_ui(url: str, keep_alive: threading.Thread | None) -> None:
    """Apre l'interfaccia e ritorna quando l'utente la chiude."""
    if open_webview(url) or open_edge_app(url):
        return
    webbrowser.open(url)
    if keep_alive is not None:  # nel browser non sappiamo quando la scheda si chiude: il server resta attivo
        keep_alive.join()


def _start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    from mosaic.api.app import create_app
    from mosaic.service import Mosaic

    app = create_app(Mosaic.open(config_mod.load()))
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning", log_config=None))
    thread = threading.Thread(target=server.run, name="mosaic-server", daemon=True)
    thread.start()
    return server, thread


def _fatal(message: str) -> int:
    log.error(message)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, f"{message}\n\nDettagli in {LOG_FILE}", TITLE, 0x10)
        except (AttributeError, OSError):
            pass
    return 1


def run(port: int | None = None) -> int:
    _setup_logging()
    _set_app_id()
    port = port or config_mod.load().port
    url = f"http://{HOST}:{port}/"
    if is_running(port):  # un'altra istanza ha già il server: apro solo una finestra
        show_ui(url, keep_alive=None)
        return 0
    try:
        server, thread = _start_server(port)
    except Exception:
        log.exception("avvio del server fallito")
        return _fatal("Mosaic non è riuscito ad avviarsi.")
    if not wait_until_ready(port):
        server.should_exit = True
        return _fatal(f"Il server di Mosaic non risponde sulla porta {port}.")
    show_ui(url, keep_alive=thread)
    server.should_exit = True
    thread.join(SHUTDOWN_TIMEOUT_S)
    return 0


if __name__ == "__main__":
    code = run()
    logging.shutdown()
    # thread non-daemon di librerie esterne (watcher, pythonnet) non devono lasciare un processo senza finestra
    os._exit(code)
