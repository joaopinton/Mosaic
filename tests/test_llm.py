import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import make_jpeg

from mosaic.index.indexer import index_roots
from mosaic.llm import captions
from mosaic.llm.captions import Caption, LlmError, describe, extract_json
from mosaic.llm.server import LlamaServer, LlmUnavailable, stop_llm
from mosaic.llm.worker import caption_photos, pending_caption_ids
from mosaic.meta import sources
from mosaic.pipeline import process_photo

FAKE = str(Path(__file__).with_name("fake_llama.py"))
GOOD = {"title": "Titolo", "caption": "Una frase.", "keywords": ["a", "b"]}


def fake_command(port: int) -> list[str]:
    return [sys.executable, FAKE, "--port", str(port)]


def reply(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def prompt_text(body: dict) -> str:
    """Tutto il testo del messaggio utente (le istruzioni stanno prima dell'immagine, nomi e contesto dopo)."""
    return " ".join(p["text"] for p in body["messages"][1]["content"] if p["type"] == "text")


def with_llm(ctx, **changes) -> None:
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, **changes))


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Ecco:\n```json\n{"a": {"b": 2}}\n```\nfine') == {"a": {"b": 2}}
    assert extract_json('prima {non json} poi {"ok": true}') == {"ok": True}
    for bad in ("", "niente", "[1,2]", "{rotto"):
        with pytest.raises(LlmError):
            extract_json(bad)


def test_caption_cleans_fields():
    c = Caption.model_validate({"title": ' "Moto   rossa" ', "caption": "x\n y", "keywords": ["#Moto", "moto", " Palco ", ""]})
    assert (c.title, c.caption, c.keywords) == ("Moto rossa", "x y", ["moto", "palco"])
    assert Caption.model_validate({"title": "t", "keywords": "uno, due;tre"}).keywords == ["uno", "due", "tre"]
    assert len(Caption.model_validate({"title": "t", "keywords": [f"k{i}" for i in range(30)]}).keywords) == 12


def test_describe_success_sends_image_and_language():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply(json.dumps(GOOD))

    result = describe("http://x", b"JPEGDATA", "it", client(handler))
    assert result.title == "Titolo"
    images = [p for p in seen["body"]["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(images) == 1 and images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "italiano" in seen["body"]["messages"][0]["content"]
    english = {}
    describe("http://x", b"x", "en", client(lambda r: (english.update(b=json.loads(r.content)), reply(json.dumps(GOOD)))[1]))
    assert "English" in english["b"]["messages"][0]["content"]


def test_describe_retries_once_with_stricter_prompt():
    prompts = []

    def handler(request):
        prompts.append(prompt_text(json.loads(request.content)))
        return reply("boh" if len(prompts) == 1 else json.dumps(GOOD))

    assert describe("http://x", b"x", "it", client(handler)).caption == "Una frase."
    assert len(prompts) == 2 and captions.STRICT_SUFFIX in prompts[1] and captions.STRICT_SUFFIX not in prompts[0]


@pytest.mark.parametrize("answer", [reply("niente json"), reply("{}"), reply('{"title": "", "caption": ""}'),
                                    httpx.Response(500, json={}), httpx.Response(200, json={"x": 1})])
def test_describe_gives_up_after_two_bad_answers(answer):
    with pytest.raises(LlmError):
        describe("http://x", b"x", "it", client(lambda r: answer))


def test_server_unavailable_without_files(ctx, tmp_path):
    ctx.models_dir = tmp_path / 'senza-modelli'   # l'LLM può essere installato davvero: qui simulo la sua assenza
    server = LlamaServer(ctx)
    assert not server.available
    with pytest.raises(LlmUnavailable):
        server.ensure()


def test_server_starts_serves_and_stops(ctx):
    server = LlamaServer(ctx, command=fake_command)
    try:
        url = server.ensure()
        assert server.running and httpx.get(f"{url}/health", timeout=3).status_code == 200
        assert server.ensure() == url  # già in esecuzione: nessun secondo processo
    finally:
        server.stop()
    assert not server.running
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{url}/health", timeout=1)


def test_server_that_dies_at_startup_raises(ctx):
    server = LlamaServer(ctx, command=lambda port: [sys.executable, "-c", "pass"])
    with pytest.raises(LlmUnavailable):
        server.ensure()
    assert not server.running


def test_idle_server_shuts_down_but_not_while_busy(ctx):
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, idle_seconds=1))
    server = LlamaServer(ctx, command=fake_command)
    try:
        server.ensure()
        with server.busy():
            time.sleep(2.2)
            assert server.running
        deadline = time.monotonic() + 8
        while server.running and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not server.running
    finally:
        server.stop()


def test_stop_llm_helper_is_safe_without_server(ctx):
    stop_llm(ctx)


def prepare(ctx, library, names):
    for n in names:
        make_jpeg(library / n, color=(n.__hash__() % 255, 40, 40), size=(300, 200))
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    return ids


def test_captions_are_applied_with_llm_source(ctx, library):
    with_llm(ctx, caption=True)
    ids = prepare(ctx, library, ["a.jpg", "b.jpg"])
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, pending_caption_ids(ctx), server=server)
    finally:
        server.stop()
    assert stats.photos == 2 and stats.failed == 0
    row = ctx.db.one("SELECT * FROM photos WHERE id=?", (ids[0],))
    assert (row["title"], row["title_src"], row["caption_src"]) == ("Moto rossa sul palco", sources.LLM, sources.LLM)
    assert row["captioned_at"]
    words = {r["word"] for r in ctx.db.all("SELECT k.word FROM photos_keywords pk JOIN keywords k ON k.id=pk.keyword_id")}
    assert words == {"moto", "palco", "rossa"}
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos_fts WHERE photos_fts MATCH 'palco'") == 2
    assert pending_caption_ids(ctx) == []
    assert len(pending_caption_ids(ctx, force=True)) == 2


def test_photos_with_xmp_title_and_caption_are_skipped(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg"])
    conn = ctx.db.conn()
    sources.set_text(conn, ids[0], "title", "Mio titolo", sources.XMP)
    sources.set_text(conn, ids[0], "caption", "Mia didascalia", sources.XMP)
    assert pending_caption_ids(ctx) == [ids[1]]


def test_llm_never_overwrites_manual_text(ctx, library):
    with_llm(ctx, caption=True)
    (pid,) = prepare(ctx, library, ["a.jpg"])
    conn = ctx.db.conn()
    sources.set_text(conn, pid, "title", "Manuale", sources.MANUAL)
    server = LlamaServer(ctx, command=fake_command)
    try:
        caption_photos(ctx, [pid], server=server)
    finally:
        server.stop()
    row = ctx.db.one("SELECT title, caption FROM photos WHERE id=?", (pid,))
    assert row["title"] == "Manuale" and row["caption"] == "Una moto rossa esposta su un palco."


def test_unusable_answers_are_counted_and_logged(ctx, library, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_MODE", "bad")
    (pid,) = prepare(ctx, library, ["a.jpg"])
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, [pid], server=server)
    finally:
        server.stop()
    assert stats.failed == 1 and stats.photos == 0
    assert ctx.db.scalar("SELECT captioned_at FROM photos WHERE id=?", (pid,)) is None
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='caption'") == 1


def test_fenced_json_answers_are_accepted(ctx, library, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_MODE", "fenced")
    (pid,) = prepare(ctx, library, ["a.jpg"])
    server = LlamaServer(ctx, command=fake_command)
    try:
        assert caption_photos(ctx, [pid], server=server).photos == 1
    finally:
        server.stop()


def test_caption_job_fails_clearly_when_llm_is_not_installed(ctx, library, tmp_path):
    (pid,) = prepare(ctx, library, ["a.jpg"])
    ctx.models_dir = tmp_path / 'senza-modelli'
    with pytest.raises(LlmUnavailable):
        caption_photos(ctx, [pid], server=LlamaServer(ctx))


def test_without_caption_only_title_and_keywords_are_written(ctx, library):
    assert ctx.cfg.llm.caption is False  # predefinito: niente frase, è la parte lenta della risposta
    pid = prepare(ctx, library, ["a.jpg"])[0]
    server = LlamaServer(ctx, command=fake_command)
    try:
        assert caption_photos(ctx, [pid], server=server).photos == 1
    finally:
        server.stop()
    row = ctx.db.one("SELECT title, title_src, caption, caption_src, captioned_at FROM photos WHERE id=?", (pid,))
    assert (row["title"], row["title_src"]) == ("Moto rossa sul palco", sources.LLM)
    assert not row["caption"] and row["caption_src"] != sources.LLM  # il finto server la manda comunque: non si scrive
    assert row["captioned_at"] and pending_caption_ids(ctx) == []


def test_short_payload_asks_only_title_and_keywords():
    body = captions.build_payload(b"x", "it", caption=False)
    schema = body["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == set(schema["properties"]) == {"title", "keywords"}
    assert body["messages"][1]["content"][0]["text"] == captions.PROMPT_SHORT["it"]
    assert "caption" not in captions.PROMPT_SHORT["it"]
    assert body["max_tokens"] < captions.build_payload(b"x", "it")["max_tokens"]
    assert "caption" in captions.SCHEMA["required"]  # lo schema completo resta intatto


def test_describe_measures_the_request():
    result = describe("http://x", b"x", "it", client(lambda r: reply(json.dumps(GOOD))), caption=False)
    assert result.elapsed > 0 and result.timings is None


# --- velocità e robustezza -----------------------------------------------------------------------

def test_payload_constrains_output_with_json_schema():
    body = captions.build_payload(b"x", "it")
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    schema = fmt["json_schema"]["schema"]
    assert set(schema["required"]) == {"title", "caption", "keywords"}
    assert schema["properties"]["keywords"]["maxItems"] == 8
    assert body["max_tokens"] <= 200


def test_prepare_image_shrinks_the_long_side(tmp_path):
    import io

    from PIL import Image

    big = make_jpeg(tmp_path / "big.jpg", size=(720, 540))
    with Image.open(io.BytesIO(captions.prepare_image(big, 448))) as img:
        assert img.size == (448, 336) and img.format == "JPEG"
    small = make_jpeg(tmp_path / "small.jpg", size=(300, 200))
    with Image.open(io.BytesIO(captions.prepare_image(small, 448))) as img:
        assert img.size == (300, 200)


def test_people_names_go_in_the_prompt_only_when_given():
    text = prompt_text
    assert "Stella, Camilla" in text(captions.build_payload(b"x", "it", people=["Stella", "Camilla"]))
    assert "da sinistra a destra" in text(captions.build_payload(b"x", "it", people=["Stella"]))
    assert "Nella foto ci sono" not in text(captions.build_payload(b"x", "it"))
    assert "Stella" in text(captions.build_payload(b"x", "en", people=["Stella"]))


def test_connection_error_means_the_server_died_and_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("rifiutata", request=request)

    with pytest.raises(captions.LlmDied):
        describe("http://x", b"x", "it", client(handler))
    assert len(calls) == 1


def test_default_threads_use_physical_cores(monkeypatch):
    from mosaic.llm import server as srv

    monkeypatch.setattr(srv.os, "cpu_count", lambda: 8)
    assert srv.default_threads() == 4
    monkeypatch.setattr(srv.os, "cpu_count", lambda: 1)
    assert srv.default_threads() == 1


def test_server_output_goes_to_a_log_file(ctx):
    server = LlamaServer(ctx, command=fake_command)
    try:
        server.ensure()
    finally:
        server.stop()
    assert "fake llama pronto" in server.log_path.read_text(encoding="utf-8")


def test_caption_restarts_the_server_once_after_a_crash(ctx, library, monkeypatch, tmp_path):
    flag = tmp_path / "muori"
    flag.write_text("1")
    monkeypatch.setenv("FAKE_LLAMA_DIE_FILE", str(flag))
    ids = prepare(ctx, library, ["a.jpg", "b.jpg"])
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, ids, server=server)
    finally:
        server.stop()
    assert stats.photos == 2 and stats.failed == 0 and stats.restarts == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='caption'") == 0


def test_caption_job_stops_when_the_server_keeps_dying(ctx, library, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_MODE", "die")
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg"])
    server = LlamaServer(ctx, command=fake_command)
    try:
        with pytest.raises(LlmUnavailable, match="llama-server.log"):
            caption_photos(ctx, ids, server=server)
    finally:
        server.stop()
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='caption'") == 0
    assert pending_caption_ids(ctx) == sorted(ids)


def test_only_confirmed_names_are_read_left_to_right(ctx, library):
    from mosaic.llm.worker import people_in_photo
    from mosaic.organize.people import ensure_subject

    (pid,) = prepare(ctx, library, ["a.jpg"])
    conn = ctx.db.conn()
    fid = conn.execute("SELECT id FROM files WHERE photo_id=?", (pid,)).fetchone()["id"]
    stella, camilla = ensure_subject(conn, "Stella"), ensure_subject(conn, "Camilla")
    for uid, x, subj, invalid in (("m1", 0.7, stella, 0), ("m2", 0.1, camilla, 0), ("m3", 0.4, None, 0),
                                  ("m4", 0.9, stella, 0), ("m5", 0.5, camilla, 1)):
        conn.execute("INSERT INTO markers(uid, file_id, photo_id, subj_id, invalid, x, y, w, h) VALUES (?,?,?,?,?,?,0,0.1,0.1)",
                     (uid, fid, pid, subj, invalid, x))
    assert people_in_photo(conn, pid) == ["Camilla", "Stella"]


def test_eta_is_formatted_in_minutes_or_hours():
    from mosaic.llm.worker import format_eta

    assert format_eta(30) == "~1 min"
    assert format_eta(38 * 60) == "~38 min"
    assert format_eta(3 * 3600 + 20 * 60) == "~3 h 20 min"


def test_fixed_instructions_come_before_the_image_so_the_server_can_cache_them():
    body = captions.build_payload(b"x", "it", people=["Stella"], context="Foto scattata il 3 maggio 2025.")
    parts = body["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "text"]
    assert parts[0]["text"] == captions.PROMPT["it"]
    assert "Stella" in parts[2]["text"] and "3 maggio" in parts[2]["text"]
    other = captions.build_payload(b"y", "it", people=["Camilla"], strict=True)
    assert other["messages"][0] == body["messages"][0] and other["messages"][1]["content"][0] == parts[0]
    assert other["messages"][1]["content"][2]["text"].endswith(captions.STRICT_SUFFIX.strip())
    assert [p["type"] for p in captions.build_payload(b"x", "it")["messages"][1]["content"]] == ["text", "image_url"]


TIMINGS = {"prompt_n": 200, "cache_n": 120, "prompt_ms": 9800.0, "predicted_n": 96, "predicted_ms": 14100.0}


def test_timings_are_read_from_the_server_reply():
    timings = captions.parse_timings({"timings": TIMINGS})
    assert timings == captions.Timings(prompt_tokens=200, cached_tokens=120, prompt_s=9.8, output_tokens=96, output_s=14.1)
    assert "96 tok" in timings.summary() and "120 in cache" in timings.summary()
    for body in ({}, {"timings": "x"}, {"timings": {"prompt_n": "tanti"}}, []):
        assert captions.parse_timings(body) is None


def test_describe_attaches_the_timings_of_the_answer():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}], "timings": TIMINGS})

    assert describe("http://x", b"x", "it", client(handler)).timings.output_tokens == 96
    assert describe("http://x", b"x", "it", client(lambda r: reply(json.dumps(GOOD)))).timings is None


def test_server_has_one_slot_per_parallel_request(ctx):
    with_llm(ctx, parallel=3, context=2048)
    cmd = LlamaServer(ctx)._build_command(1234)
    assert cmd[cmd.index("-np") + 1] == "3"
    assert cmd[cmd.index("-c") + 1] == "6144"  # il contesto si divide tra gli slot: 2048 token per richiesta


def test_parallel_requests_overlap_and_caption_every_photo(ctx, library, monkeypatch, tmp_path):
    peak = tmp_path / "picco"
    monkeypatch.setenv("FAKE_LLAMA_DELAY", "0.4")
    monkeypatch.setenv("FAKE_LLAMA_PEAK_FILE", str(peak))
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"])
    with_llm(ctx, parallel=3)
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, ids, server=server)
    finally:
        server.stop()
    assert stats.photos == 5 and stats.failed == 0 and stats.restarts == 0
    assert pending_caption_ids(ctx) == []
    assert int(peak.read_text()) >= 2  # le richieste arrivano davvero insieme al server


def test_parallel_crash_restarts_once_and_loses_no_photo(ctx, library, monkeypatch, tmp_path):
    flag = tmp_path / "muori"
    flag.write_text("1")
    monkeypatch.setenv("FAKE_LLAMA_DIE_FILE", str(flag))
    monkeypatch.setenv("FAKE_LLAMA_DELAY", "0.2")
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    with_llm(ctx, parallel=2)
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, ids, server=server)
    finally:
        server.stop()
    assert stats.photos == 4 and stats.failed == 0 and stats.restarts == 1
    assert pending_caption_ids(ctx) == []  # ogni foto ha la sua didascalia: nessuna persa, nessuna rifatta al posto di un'altra
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='caption'") == 0


class StopAtFirstProgress:
    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.messages: list[str] = []

    def progress(self, value: float, message: str = "", *_counts: int | None) -> None:
        self.messages.append(message)
        self.cancel.set()


def test_cancel_stops_at_once_without_waiting_for_photos_in_flight(ctx, library):
    ids = prepare(ctx, library, ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"])
    with_llm(ctx, parallel=2)
    control = StopAtFirstProgress()
    server = LlamaServer(ctx, command=fake_command)
    before = len(pending_caption_ids(ctx))
    try:
        stats = caption_photos(ctx, ids, control, server=server)
    finally:
        server.stop()
    # la foto finita resta; quella ancora in volo si abbandona (aspettarla costava fino a mezzo minuto) e si rifà dopo
    assert 1 <= stats.photos <= 2  # al massimo le due già in volo: nessuna foto nuova parte dopo l'annullamento
    assert len(pending_caption_ids(ctx)) == before - stats.photos
    assert control.messages[0].startswith("1/")
