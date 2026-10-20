"""Finto llama-server per i test: GET /health e POST /v1/chat/completions. Modalità via FAKE_LLAMA_MODE (good, fenced, bad, die);
FAKE_LLAMA_DIE_FILE fa morire il processo alla prima richiesta finché il file esiste. Come llama-server con più slot
risponde a più richieste insieme: FAKE_LLAMA_DELAY (secondi) allunga ogni risposta e FAKE_LLAMA_PEAK_FILE riceve il
numero massimo di richieste servite contemporaneamente."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOD = {"title": "Moto rossa sul palco", "caption": "Una moto rossa esposta su un palco.", "keywords": ["Moto", "#palco", "rossa"]}
TIMINGS = {"prompt_n": 180, "cache_n": 120, "prompt_ms": 900.0, "predicted_n": 40, "predicted_ms": 2000.0}

_lock = threading.Lock()
_active = 0
_peak = 0


def _enter() -> None:
    global _active, _peak
    with _lock:
        _active += 1
        _peak = max(_peak, _active)
        peak_file = os.environ.get("FAKE_LLAMA_PEAK_FILE")
        if peak_file:
            with open(peak_file, "w") as f:
                f.write(str(_peak))


def _leave() -> None:
    global _active
    with _lock:
        _active -= 1


def _should_die(mode: str) -> bool:
    die_file = os.environ.get("FAKE_LLAMA_DIE_FILE")
    if mode == "die":
        return True
    if not die_file:
        return False
    try:
        os.remove(die_file)  # muore una volta sola: il processo riavviato risponde
    except FileNotFoundError:
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"status": "ok"}) if self.path == "/health" else self._send(404, {})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        mode = os.environ.get("FAKE_LLAMA_MODE", "good")
        _enter()
        try:
            time.sleep(float(os.environ.get("FAKE_LLAMA_DELAY", "0")))
            if _should_die(mode):
                os._exit(1)
            self._answer(mode)
        finally:
            _leave()

    def _answer(self, mode):
        content = {
            "good": json.dumps(GOOD),
            "fenced": "Ecco:\n```json\n" + json.dumps(GOOD) + "\n```",
            "bad": "non so descrivere questa foto",
        }[mode]
        self._send(200, {"choices": [{"message": {"content": content}}], "timings": TIMINGS})


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1])
    print("fake llama pronto", file=sys.stderr, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
