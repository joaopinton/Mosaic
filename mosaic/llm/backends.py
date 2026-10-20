"""Dove si generano le didascalie: servizi cloud gratuiti (se l'utente li ha attivati) e, per ultimo, llama-server locale.

I servizi cloud hanno limiti di richieste al minuto: ogni backend tiene il proprio ritmo e il proprio riposo dopo un
429 e, se la chiave o il modello vengono rifiutati, smette di essere usato per il resto del giro.
Il modello locale entra in gioco solo quando nessun servizio cloud è utilizzabile.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass, field

import httpx

from mosaic.ai.analyze import release_models
from mosaic.context import Context
from mosaic.llm import captions, secrets
from mosaic.llm.captions import Caption
from mosaic.llm.server import LlamaServer, server_for

log = logging.getLogger(__name__)

NO_CAPTIONER = ("Nessun modello per le didascalie: installa quello locale (`python -m mosaic.assets fetch --group llm`) "
                "oppure attiva il cloud in Impostazioni e inserisci una chiave")
TRANSIENT_COOLDOWN = 120.0  # errore del server o rete assente: pausa iniziale, raddoppia a ogni errore di fila
MAX_COOLDOWN = 1800.0


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    url: str
    rpm: int  # richieste al minuto, un po' sotto il limite gratuito del servizio
    headers: dict[str, str] = field(default_factory=dict)


PROVIDERS = {
    "nvidia": ProviderSpec("NVIDIA", "https://integrate.api.nvidia.com", 35),
    "openrouter": ProviderSpec("OpenRouter", "https://openrouter.ai/api", 18, {"X-Title": "Mosaic"}),
}


@dataclass(frozen=True)
class Request:
    """Una foto pronta da descrivere: immagine, nomi delle persone e contesto sono già composti."""
    image: bytes
    people: list[str]
    note: str
    caption: bool = True  # anche la frase di didascalia, oltre a titolo e parole chiave (`llm.caption`)


class Backend:
    """Stato di un backend. Si tocca solo dal thread principale: nel pool gira soltanto `describe`."""

    name = ""
    label = ""
    is_cloud = False

    def __init__(self, parallel: int) -> None:
        self.parallel = parallel
        self.in_flight = 0
        self.cooldown_until = 0.0
        self.next_at = 0.0
        self.strikes = 0      # errori di fila senza una risposta riuscita in mezzo
        self.dead = False
        self.interval = 0.0   # secondi minimi tra due richieste

    def alive(self, now: float) -> bool:
        return not self.dead and now >= self.cooldown_until

    def ready(self, now: float) -> bool:
        return self.alive(now) and self.in_flight < self.parallel and now >= self.next_at

    def reserve(self, now: float) -> None:
        self.in_flight += 1
        self.next_at = now + self.interval

    def release(self) -> None:
        self.in_flight -= 1

    def succeeded(self) -> None:
        self.strikes = 0

    def rate_limited(self, now: float, retry_after: int) -> float:
        """Mette il backend a riposo; a ogni 429 di fila la pausa raddoppia. Ritorna i secondi di pausa.

        Più richieste in volo insieme ricevono lo stesso 429: quelle che arrivano a backend già a riposo non contano.
        """
        if now < self.cooldown_until:
            return self.cooldown_until - now
        pause = min(retry_after * 2 ** self.strikes, captions.MAX_RETRY_AFTER)
        self.strikes += 1
        self.cooldown_until = now + pause
        return pause

    def failed(self, now: float, error: captions.BackendDown) -> float | None:
        """Pausa dopo un errore del servizio; None se è definitivo (chiave o modello rifiutati)."""
        if error.fatal:
            self.dead = True
            return None
        if now < self.cooldown_until:
            return self.cooldown_until - now
        pause = min(TRANSIENT_COOLDOWN * 2 ** self.strikes, MAX_COOLDOWN)
        self.strikes += 1
        self.cooldown_until = now + pause
        return pause

    def start(self, stack: ExitStack) -> None:
        """Prepara il backend alla prima richiesta (il server locale si avvia qui, non prima)."""

    def describe(self, request: Request, language: str, client: httpx.Client) -> Caption:
        raise NotImplementedError


class CloudBackend(Backend):
    is_cloud = True

    def __init__(self, provider: str, key: str, model: str, parallel: int, send_names: bool) -> None:
        super().__init__(parallel)
        spec = PROVIDERS[provider]
        self.name = provider
        self.label = spec.label
        self.model = model
        self.send_names = send_names
        self.url = spec.url
        self.interval = 60.0 / spec.rpm
        self._headers = {"Authorization": f"Bearer {key}", **spec.headers}

    def describe(self, request: Request, language: str, client: httpx.Client) -> Caption:
        people = request.people if self.send_names else []
        return captions.describe(self.url, request.image, language, client, people, request.note,
                                 cloud=True, model=self.model, headers=self._headers, caption=request.caption)


class LocalBackend(Backend):
    name = "locale"
    label = "modello locale"

    def __init__(self, ctx: Context, server: LlamaServer) -> None:
        super().__init__(max(1, ctx.cfg.llm.parallel))
        self.ctx = ctx
        self.server = server
        self._started = False

    def start(self, stack: ExitStack) -> None:
        if self._started:
            return
        release_models(self.ctx)  # RAM: SigLIP e LLM non convivono
        stack.enter_context(self.server.busy())
        self.server.ensure()
        self._started = True

    def describe(self, request: Request, language: str, client: httpx.Client) -> Caption:
        return captions.describe(self.server.base_url, request.image, language, client, request.people, request.note,
                                 caption=request.caption)


def cloud_backends(ctx: Context) -> list[CloudBackend]:
    """I servizi cloud attivati e con una chiave, nell'ordine scelto. Vuoto se il cloud è spento (predefinito)."""
    cfg = ctx.cfg.llm.cloud
    if not cfg.enabled:
        return []
    models = {"nvidia": cfg.nvidia_model, "openrouter": cfg.openrouter_model}
    found: list[CloudBackend] = []
    for provider in cfg.providers:
        key = secrets.get_key(ctx.data_dir, provider)
        if key:
            found.append(CloudBackend(provider, key, models[provider], cfg.parallel, cfg.send_names))
    return found


def build_chain(ctx: Context, server: LlamaServer | None = None) -> list[Backend]:
    """Cloud nell'ordine scelto, poi il modello locale (se installato) come ripiego."""
    chain: list[Backend] = list(cloud_backends(ctx))
    local = server or server_for(ctx)
    if local.available:
        chain.append(LocalBackend(ctx, local))
    return chain


def captioning_available(ctx: Context) -> bool:
    """Si possono generare didascalie: modello locale installato oppure cloud attivo con almeno una chiave."""
    return bool(cloud_backends(ctx)) or server_for(ctx).available
