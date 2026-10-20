"""Catena di backend per le didascalie: cloud nell'ordine scelto, poi il modello locale."""

import json
import threading
import time
from dataclasses import replace

import httpx
import pytest
from test_llm import fake_command, prepare
from test_llm_cloud import IMAGE, client, ok

from mosaic.llm import secrets
from mosaic.llm.backends import CloudBackend, LocalBackend, Request, build_chain, captioning_available
from mosaic.llm.server import LlamaServer, LlmUnavailable
from mosaic.llm.worker import caption_photos, pending_caption_ids

NVIDIA, OPENROUTER = "integrate.api.nvidia.com", "openrouter.ai"
TIMEOUT = -1  # stato finto: il servizio non risponde in tempo


class Cloud:
    """Finto cloud: risposta per host (200, oppure un codice di errore) e conteggio delle richieste."""

    def __init__(self, **status: int) -> None:
        self.status = {NVIDIA: 200, OPENROUTER: 200} | status
        self.calls = {NVIDIA: 0, OPENROUTER: 0}
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in self.calls:  # il server locale (fake_llama) è vero: la richiesta passa dalla rete
            return httpx.post(request.url, content=request.content, headers={"content-type": "application/json"},
                              timeout=60)
        self.calls[host] += 1
        self.bodies.append(json.loads(request.content))
        code = self.status[host]
        if code == TIMEOUT:
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        if code == 200:
            return ok()
        return httpx.Response(code, headers={"Retry-After": "30"} if code == 429 else {})

    def client(self) -> httpx.Client:
        return client(self)


def backend(provider: str, parallel: int = 4, send_names: bool = True) -> CloudBackend:
    b = CloudBackend(provider, "chiave", "modello", parallel, send_names)
    b.interval = 0  # nei test il ritmo del servizio non serve
    return b


def run(ctx, ids, chain, cloud, **kwargs):
    return caption_photos(ctx, ids, chain=chain, client=cloud.client(), **kwargs)


def with_cloud(ctx, **changes) -> None:
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, cloud=replace(ctx.cfg.llm.cloud, **changes)))


def test_cloud_describes_every_photo_and_never_starts_the_local_model(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    cloud = Cloud()
    server = LlamaServer(ctx, command=fake_command)
    stats = run(ctx, ids, [backend("nvidia"), LocalBackend(ctx, server)], cloud)
    assert stats.by_backend == {"nvidia": 3} and stats.failed == 0
    assert not server.running and cloud.calls[OPENROUTER] == 0
    assert pending_caption_ids(ctx) == []
    assert stats.where() == " (nvidia 3)"


def test_rate_limited_service_hands_its_photos_to_the_next_one(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    cloud = Cloud(**{NVIDIA: 429})
    stats = run(ctx, ids, [backend("nvidia"), backend("openrouter")], cloud)
    assert stats.by_backend == {"openrouter": 4} and stats.failed == 0
    assert cloud.calls[NVIDIA] <= 4  # solo la prima raffica: poi riposa per tutto il giro
    assert pending_caption_ids(ctx) == []


def test_a_burst_of_429s_counts_as_one_strike():
    b = backend("nvidia")
    first = b.rate_limited(100.0, 30)
    again = b.rate_limited(100.5, 30)
    assert first == 30 and b.strikes == 1 and again == pytest.approx(29.5)
    assert b.rate_limited(131.0, 30) == 60  # un nuovo 429 dopo il riposo raddoppia la pausa


def test_rejected_key_is_logged_and_the_service_is_not_used_again(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    cloud = Cloud(**{NVIDIA: 401})
    stats = run(ctx, ids, [backend("nvidia", parallel=1), backend("openrouter")], cloud)
    assert stats.by_backend == {"openrouter": 3}
    assert cloud.calls[NVIDIA] == 1
    row = ctx.db.one("SELECT ref, message FROM errors WHERE scope='caption'")
    assert row["ref"] == "nvidia" and "Impostazioni" in row["message"]


def test_all_cloud_down_falls_back_to_the_local_model_only_then(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    cloud = Cloud(**{NVIDIA: 429, OPENROUTER: 503})
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = run(ctx, ids, [backend("nvidia"), backend("openrouter"), LocalBackend(ctx, server)], cloud)
    finally:
        server.stop()
    assert stats.by_backend == {"locale": 3} and stats.failed == 0
    assert pending_caption_ids(ctx) == []


def test_silent_service_hands_its_photos_to_the_local_model_without_failures(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    cloud = Cloud(**{NVIDIA: TIMEOUT})
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = run(ctx, ids, [backend("nvidia", parallel=1), LocalBackend(ctx, server)], cloud)
    finally:
        server.stop()
    assert stats.by_backend == {"locale": 3} and stats.failed == 0
    assert cloud.calls[NVIDIA] == 1  # un solo tentativo, poi il servizio riposa per tutto il giro
    assert pending_caption_ids(ctx) == []
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='caption'") == 0


def test_without_local_model_and_no_working_cloud_the_job_fails_clearly(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg"])
    cloud = Cloud(**{NVIDIA: 401})
    with pytest.raises(LlmUnavailable, match="NVIDIA"):
        run(ctx, ids, [backend("nvidia")], cloud)
    assert sorted(pending_caption_ids(ctx)) == sorted(ids)  # nessuna foto persa né segnata come fallita


def test_service_rests_while_the_local_model_keeps_working(ctx, library, monkeypatch):
    monkeypatch.setattr("mosaic.llm.backends.TRANSIENT_COOLDOWN", 0.2)
    monkeypatch.setenv("FAKE_LLAMA_DELAY", "0.3")
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"])
    cloud = Cloud(**{NVIDIA: 503})
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = run(ctx, ids, [backend("nvidia", parallel=1), LocalBackend(ctx, server)], cloud)
    finally:
        server.stop()
    assert stats.photos == 6 and stats.by_backend["locale"] >= 1
    assert cloud.calls[NVIDIA] >= 2  # dopo il riposo il servizio viene riprovato


def test_requests_respect_the_services_pace(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    paced = backend("nvidia")
    paced.interval = 0.3
    started = time.monotonic()
    stats = run(ctx, ids, [paced], Cloud())
    assert stats.photos == 3 and time.monotonic() - started >= 0.6


def test_cancel_during_a_rest_returns_promptly(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg"])

    class Control:
        cancel = threading.Event()

        def progress(self, value, message="", *_counts):
            pass

    control = Control()
    # un solo backend, a riposo per 30 s dopo il primo 429: senza annullo il giro aspetterebbe
    threading.Timer(0.5, control.cancel.set).start()
    started = time.monotonic()
    stats = run(ctx, ids, [backend("nvidia", parallel=1)], Cloud(**{NVIDIA: 429}), control=control)
    assert time.monotonic() - started < 10 and stats.photos == 0
    assert sorted(pending_caption_ids(ctx)) == sorted(ids)


@pytest.mark.parametrize("send_names,present", [(True, True), (False, False)])
def test_names_reach_the_cloud_only_when_allowed(send_names, present):
    cloud = Cloud()
    backend("nvidia", send_names=send_names).describe(Request(IMAGE, ["Anna"], "Contesto"), "it", cloud.client())
    sent = json.dumps(cloud.bodies[0], ensure_ascii=False)
    assert ("Anna" in sent) is present and "Contesto" in sent


def test_cloud_is_off_by_default_even_with_keys_saved(ctx):
    secrets.set_key(ctx.data_dir, "nvidia", "k")
    secrets.set_key(ctx.data_dir, "openrouter", "k")
    assert not any(b.is_cloud for b in build_chain(ctx, LlamaServer(ctx, command=fake_command)))


def test_chain_follows_the_configured_order_and_ends_with_the_local_model(ctx):
    secrets.set_key(ctx.data_dir, "nvidia", "k")
    secrets.set_key(ctx.data_dir, "openrouter", "k")
    with_cloud(ctx, enabled=True, providers=("openrouter", "nvidia"))
    chain = build_chain(ctx, LlamaServer(ctx, command=fake_command))
    assert [b.name for b in chain] == ["openrouter", "nvidia", "locale"]


def test_service_without_a_key_is_left_out(ctx):
    secrets.set_key(ctx.data_dir, "openrouter", "k")
    with_cloud(ctx, enabled=True)
    assert [b.name for b in build_chain(ctx, LlamaServer(ctx, command=fake_command))] == ["openrouter", "locale"]


def test_captioning_is_available_with_only_a_cloud_key(ctx, tmp_path, monkeypatch):
    ctx.models_dir = tmp_path / "vuoto"
    assert not captioning_available(ctx)
    secrets.set_key(ctx.data_dir, "nvidia", "k")
    assert not captioning_available(ctx)  # chiave presente ma cloud spento
    with_cloud(ctx, enabled=True)
    assert captioning_available(ctx)
    assert [b.name for b in build_chain(ctx)] == ["nvidia"]
