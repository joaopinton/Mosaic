"""Titolo, parole chiave e (se richiesta) didascalia in italiano da un LLM visivo (API compatibile OpenAI di llama.cpp)."""

from __future__ import annotations

import base64
import io
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

REQUEST_TIMEOUT = 600.0  # su CPU una foto può richiedere decine di secondi
CLOUD_TIMEOUT = 60.0     # il cloud risponde in 10-35 s; oltre è in coda o bloccato e la foto passa al servizio successivo
MAX_TOKENS = 200         # titolo + frase + 8 parole chiave stanno in circa 120 token
MAX_TOKENS_SHORT = 120   # senza la frase: titolo + 8 parole chiave stanno in circa 60 token
ATTEMPTS = 2
DEFAULT_RETRY_AFTER = 60   # secondi di pausa dopo un 429 senza Retry-After leggibile
MAX_RETRY_AFTER = 3600
FATAL_STATUSES = (401, 402, 403, 404)  # chiave rifiutata, credito finito, modello ritirato: inutile riprovare
JPEG_QUALITY = 85
MAX_KEYWORDS = 8

SYSTEM = {
    "it": "Sei un assistente che descrive fotografie in italiano. Rispondi SOLO con un oggetto JSON valido, senza altro testo.",
    "en": "You describe photographs in English. Reply ONLY with a valid JSON object and nothing else.",
}
PROMPT = {
    "it": (
        'Descrivi questa foto in JSON {"title", "caption", "keywords"}. title: al massimo 8 parole. '
        "caption: una frase in voce attiva, al massimo 25 parole. keywords: da 3 a 8 parole in minuscolo. "
        "Non inventare nomi di persone né testi che non vedi."
    ),
    "en": (
        'Describe this photo as JSON {"title", "caption", "keywords"}. title: at most 8 words. '
        "caption: one sentence in active voice, at most 25 words. keywords: 3 to 8 lowercase words. "
        "Do not invent names of people or text you cannot see."
    ),
}
# Senza didascalia: la frase è circa metà dei token generati, e generarli è la parte lenta della risposta
PROMPT_SHORT = {
    "it": (
        'Descrivi questa foto in JSON {"title", "keywords"}. title: al massimo 8 parole. '
        "keywords: da 3 a 8 parole in minuscolo. Non inventare nomi di persone né testi che non vedi."
    ),
    "en": (
        'Describe this photo as JSON {"title", "keywords"}. title: at most 8 words. '
        "keywords: 3 to 8 lowercase words. Do not invent names of people or text you cannot see."
    ),
}
PEOPLE = {
    "it": " Nella foto ci sono, da sinistra a destra: {names}. Usa questi nomi se servono; non inventarne altri.",
    "en": " The people in the photo, from left to right, are: {names}. Use these names if relevant; do not invent others.",
}
STRICT_SUFFIX = " Rispondi esclusivamente con il JSON, senza spiegazioni né blocchi di codice."

# llama-server converte lo schema in una grammatica: la risposta è sempre JSON valido nel formato atteso
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_KEYWORDS},
    },
    "required": ["title", "caption", "keywords"],
    "additionalProperties": False,
}
SCHEMA_SHORT = {
    **SCHEMA,
    "properties": {k: v for k, v in SCHEMA["properties"].items() if k != "caption"},
    "required": ["title", "keywords"],
}


class LlmError(RuntimeError):
    """La risposta dell'LLM non è utilizzabile."""


class LlmDied(LlmError):
    """llama-server non risponde più: il processo si è chiuso o la connessione è caduta."""


class RateLimited(LlmError):
    """Il servizio cloud ha risposto 429: quel servizio va lasciato riposare `retry_after` secondi."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"troppe richieste, riprovare tra {retry_after} s")
        self.retry_after = retry_after


class BackendDown(LlmError):
    """Il servizio cloud non è utilizzabile: chiave o modello rifiutati (FATAL_STATUSES) o errore del server (5xx, rete: status 0)."""

    @property
    def fatal(self) -> bool:
        return self.status in FATAL_STATUSES

    def __init__(self, status: int, reason: str = "") -> None:
        super().__init__(reason or f"HTTP {status}")
        self.status = status


def parse_retry_after(value: str | None) -> int:
    """Secondi dall'intestazione Retry-After (solo la forma numerica), tenuti entro limiti ragionevoli."""
    try:
        seconds = int(float(value or ""))
    except ValueError:
        return DEFAULT_RETRY_AFTER
    return min(max(seconds, 1), MAX_RETRY_AFTER)


@dataclass(frozen=True)
class Timings:
    """Dove va il tempo di una risposta (`timings` di llama-server)."""
    prompt_tokens: int   # token del prompt calcolati, immagine compresa
    cached_tokens: int   # token del prompt riusati dalla cache del server
    prompt_s: float
    output_tokens: int
    output_s: float

    def summary(self) -> str:
        rate = self.output_tokens / self.output_s if self.output_s > 0 else 0.0
        return (f"prompt {self.prompt_tokens} tok ({self.cached_tokens} in cache) {self.prompt_s:.1f} s · "
                f"risposta {self.output_tokens} tok {self.output_s:.1f} s ({rate:.1f} tok/s)")


def parse_timings(body: object) -> Timings | None:
    """Tempi della risposta di llama-server; None se il server non li manda o sono malformati."""
    data = body.get("timings") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    try:
        return Timings(int(data.get("prompt_n", 0)), int(data.get("cache_n", 0)), float(data.get("prompt_ms", 0)) / 1000,
                       int(data.get("predicted_n", 0)), float(data.get("predicted_ms", 0)) / 1000)
    except (TypeError, ValueError):
        return None


class Caption(BaseModel):
    title: str = Field(default="", max_length=160)
    caption: str = Field(default="", max_length=600)
    keywords: list[str] = Field(default_factory=list)
    _timings: Timings | None = PrivateAttr(default=None)
    _elapsed: float = PrivateAttr(default=0.0)

    @property
    def timings(self) -> Timings | None:
        return self._timings

    @property
    def elapsed(self) -> float:
        """Secondi della richiesta riuscita, dall'invio alla risposta: per il cloud è l'unico tempo che si conosce."""
        return self._elapsed

    @field_validator("title", "caption", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().strip('"')[:600]

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: object) -> list[str]:
        items = value if isinstance(value, list) else re.split(r"[;,]", str(value or ""))
        out: list[str] = []
        for item in items:
            word = str(item).strip().lstrip("#").lower()
            if word and len(word) <= 60 and word not in out:
                out.append(word)
        return out[:12]


def extract_json(text: str) -> dict:
    """Primo oggetto JSON nel testo (anche dentro blocchi di codice o circondato da parole)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
                if isinstance(data, dict):
                    return data
                break
        start = text.find("{", start + 1)
    raise LlmError("nessun JSON nella risposta")


def build_payload(image: bytes, language: str, strict: bool = False, people: Sequence[str] = (),
                  context: str = "", model: str | None = None, schema: bool = True, caption: bool = True) -> dict:
    """`people`: nomi confermati delle persone nella foto, da sinistra a destra. `context`: testo già composto (context.py).

    `model`: nome del modello, richiesto dai servizi cloud (llama-server ne serve uno solo). `schema=False` toglie la
    grammatica JSON, che è una funzione di llama-server: il cloud si affida al prompt e a `extract_json`.
    `caption=False` chiede solo titolo e parole chiave (`llm.caption`).
    """
    lang = language if language in PROMPT else "it"
    data_uri = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
    variable = ""
    if people:
        variable += PEOPLE[lang].format(names=", ".join(people))
    if context:
        variable += " " + context
    if strict:
        variable += STRICT_SUFFIX
    # Istruzioni fisse prima dell'immagine: system e istruzioni sono lo stesso prefisso per ogni foto e llama-server
    # lo riusa dalla cache invece di ricalcolarlo. Nomi e contesto, che cambiano da foto a foto, vanno dopo.
    instructions = (PROMPT if caption else PROMPT_SHORT)[lang]
    content: list[dict] = [{"type": "text", "text": instructions}, {"type": "image_url", "image_url": {"url": data_uri}}]
    if variable.strip():
        content.append({"type": "text", "text": variable.strip()})
    payload: dict = {
        "messages": [
            {"role": "system", "content": SYSTEM[lang]},
            {"role": "user", "content": content},
        ],
        "max_tokens": MAX_TOKENS if caption else MAX_TOKENS_SHORT,
        "temperature": 0.2,
    }
    if schema:
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "caption", "schema": SCHEMA if caption else SCHEMA_SHORT}}
    if model:
        payload["model"] = model
    return payload


def describe(base_url: str, image: bytes, language: str = "it", client: httpx.Client | None = None,
             people: Sequence[str] = (), context: str = "", *, cloud: bool = False, model: str | None = None,
             headers: dict[str, str] | None = None, caption: bool = True) -> Caption:
    """Chiede all'LLM titolo, parole chiave e, con `caption`, la didascalia. Un nuovo tentativo se il JSON non è valido.

    Se il server locale non risponde più solleva subito LlmDied: ritentare sullo stesso server sarebbe inutile.
    Con `cloud` (servizio esterno con chiave in `headers`) senza grammatica JSON e con CLOUD_TIMEOUT; 429 solleva
    RateLimited e 401/402/403/404/5xx, rete assente o nessuna risposta in tempo BackendDown, senza ritentare: spetta al
    chiamante passare a un altro servizio.
    """
    own = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    last_error = "risposta vuota"
    try:
        for attempt in range(ATTEMPTS):
            try:
                sent = time.monotonic()
                resp = http.post(f"{base_url}/v1/chat/completions", headers=headers,
                                 json=build_payload(image, language, attempt > 0, people, context, model, not cloud, caption),
                                 timeout=CLOUD_TIMEOUT if cloud else httpx.USE_CLIENT_DEFAULT)
                _check_status(resp, cloud)
                resp.raise_for_status()
                body = resp.json()
                result = Caption.model_validate(extract_json(body["choices"][0]["message"]["content"]))
                result._timings = parse_timings(body)
                result._elapsed = time.monotonic() - sent
            except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if cloud:
                    raise BackendDown(0, "servizio non raggiungibile") from exc  # rete assente: stesso trattamento
                raise LlmDied(f"{type(exc).__name__}: {exc}") from exc
            except httpx.TimeoutException as exc:
                if cloud:  # ritentare costerebbe un altro minuto: meglio un altro servizio o il modello locale
                    raise BackendDown(0, f"nessuna risposta in {CLOUD_TIMEOUT:.0f} s") from exc
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            except (RateLimited, BackendDown):
                raise
            except (httpx.HTTPError, KeyError, IndexError, ValueError, LlmError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if result.title or result.caption:
                return result
            last_error = "titolo e didascalia vuoti"
    finally:
        if own:
            http.close()
    raise LlmError(f"risposta non valida dopo {ATTEMPTS} tentativi ({last_error})")


def _check_status(resp: httpx.Response, cloud: bool) -> None:
    if resp.status_code == 429:
        raise RateLimited(parse_retry_after(resp.headers.get("Retry-After")))
    if cloud and (resp.status_code in FATAL_STATUSES or resp.status_code >= 500):
        raise BackendDown(resp.status_code)


def prepare_image(path: Path, max_side: int) -> bytes:
    """JPEG con il lato lungo al massimo `max_side`: meno pixel, meno token visivi, risposta più rapida."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()
