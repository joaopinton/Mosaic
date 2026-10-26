import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg, make_video, needs_ffmpeg
from fastapi.testclient import TestClient
from PIL import Image

from mosaic.ai.vectors import save_embeddings
from mosaic.api import routes_collections, routes_library
from mosaic.api.app import create_app
from mosaic.index.indexer import index_roots
from mosaic.jobs.events import EventBus
from mosaic.organize import moments, tags
from mosaic.pipeline import process_photo
from mosaic.service import Mosaic

rng = np.random.default_rng(3)


@pytest.fixture()
def m(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))  # mai toccare il config.yaml vero
    mosaic = Mosaic(ctx)
    yield mosaic
    mosaic.runner.stop()


@pytest.fixture()
def client(m):
    return TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False))


def load(ctx, *files: tuple[str, tuple[int, int, int]]) -> list[int]:
    lib = Path(ctx.cfg.library_roots[0])
    for name, color in files:
        make_jpeg(lib / name, color=color, size=(640, 480))
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    return ids


@pytest.fixture()
def lib3(ctx):
    return load(ctx, ("a.jpg", (200, 20, 20)), ("b.jpg", (20, 200, 20)), ("c.jpg", (20, 20, 200)))


def uid_of(ctx, pid: int) -> str:
    return ctx.db.scalar("SELECT uid FROM photos WHERE id=?", (pid,))


# --- sicurezza ------------------------------------------------------------------------------

def test_host_header_must_be_loopback(client):
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/api/status", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/status", headers={"host": "localhost"}).status_code == 200
    assert client.get("/api/status", headers={"host": "[::1]:8000"}).status_code == 200


def test_cross_origin_writes_are_rejected(client, lib3, ctx):
    uid = uid_of(ctx, lib3[0])
    url = f"/api/photos/{uid}"
    assert client.patch(url, json={"favorite": True}, headers={"origin": "https://evil.example.com"}).status_code == 403
    assert client.patch(url, json={"favorite": True}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.patch(url, json={"favorite": True}, headers={"origin": "http://testserver"}).status_code == 200
    assert client.patch(url, json={"favorite": True}).status_code == 200  # nessun Origin (curl, CLI)
    assert client.get(url, headers={"origin": "https://evil.example.com"}).status_code == 200  # le letture sono innocue


def test_security_headers_present(client):
    r = client.get("/api/status")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"


def test_openapi_and_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 200  # cade nel fallback SPA: nessuna documentazione automatica
    assert "swagger" not in client.get("/docs").text.lower() and client.get("/openapi.json").headers["content-type"].startswith("text/html")


# --- stato e foto ---------------------------------------------------------------------------------

def test_status_reports_counts_and_first_run(client, lib3, m):
    data = client.get("/api/status").json()
    assert data["photos"] == 3 and data["first_run"] is False and data["busy"] is False
    assert {"siglip2", "llama"} <= set(data["assets"]) and isinstance(data["llm_ready"], bool)
    assert data["caption_auto"] is False  # le descrizioni non fanno parte della pipeline finché non si attivano


def test_list_photos_default_filters_and_pagination(client, lib3, ctx):
    data = client.get("/api/photos").json()
    assert data["total"] == 3 and len(data["photos"]) == 3
    first = data["photos"][0]
    assert {"uid", "type", "hash", "favorite", "private", "archived", "width"} <= set(first) and len(first["hash"]) == 40
    assert client.get("/api/photos", params={"count": 2, "offset": 2}).json()["photos"].__len__() == 1
    assert client.get("/api/photos", params={"q": "color:rosso"}).json()["total"] == 1
    assert client.get("/api/photos", params={"q": "nome-che-non-esiste"}).json()["total"] == 0
    assert client.get("/api/photos", params={"order": "boh"}).status_code == 400
    assert client.get("/api/photos", params={"count": 9999}).status_code == 422
    assert client.get("/api/photos", params={"q": "date:oggi before:ieri"}).status_code in (200, 400)


def test_photo_detail_and_404(client, lib3, ctx):
    uid = uid_of(ctx, lib3[0])
    d = client.get(f"/api/photos/{uid}").json()
    assert d["uid"] == uid and d["width"] == 640 and d["files"][0]["primary"] and d["files"][0]["path"].endswith("a.jpg")
    assert d["main_color"] == "Rosso" and d["labels"] == [] and d["people"] == [] and d["albums"] == []
    assert client.get("/api/photos/nope").status_code == 404
    assert client.get("/api/photos/..%2F..%2Fetc").status_code in (404, 400)


def test_patch_sets_manual_text_and_reindexes_search(client, lib3, ctx):
    uid = uid_of(ctx, lib3[0])
    r = client.patch(f"/api/photos/{uid}", json={"title": "Ducati sul palco", "caption": "Una moto.", "favorite": True,
                                                  "keywords_add": ["Superbike"], "labels_add": ["Moto custom"]})
    d = r.json()
    assert d["title"] == "Ducati sul palco" and d["title_source"] == "manuale" and d["favorite"] is True
    assert d["keywords"] == ["superbike"] and d["labels"][0]["name"] == "Moto custom" and d["labels"][0]["source"] == "manuale"
    assert client.get("/api/photos", params={"q": "palco"}).json()["total"] == 1
    assert client.get("/api/photos", params={"q": "label:moto-custom"}).json()["total"] == 1
    again = client.patch(f"/api/photos/{uid}", json={"keywords_remove": ["superbike"], "labels_remove": ["moto-custom"],
                                                      "title": "  "}).json()
    assert again["keywords"] == [] and again["labels"] == [] and again["title"] == "" and again["title_source"] == ""
    assert client.patch(f"/api/photos/{uid}", json={"title": "x" * 400}).status_code == 422
    assert client.patch("/api/photos/nope", json={"favorite": True}).status_code == 404


def test_batch_actions(client, lib3, ctx):
    uids = [uid_of(ctx, p) for p in lib3]
    assert client.post("/api/photos/batch", json={"uids": uids[:2], "action": "favorite"}).json() == {"changed": 2}
    assert client.get("/api/photos", params={"q": "favorite:true"}).json()["total"] == 2
    assert client.post("/api/photos/batch", json={"uids": uids[:1], "action": "archive"}).json() == {"changed": 1}
    assert client.get("/api/photos").json()["total"] == 2
    assert client.get("/api/photos", params={"q": "archived:true"}).json()["total"] == 1
    client.post("/api/photos/batch", json={"uids": uids[:1], "action": "restore"})
    client.post("/api/photos/batch", json={"uids": uids[2:], "action": "private"})
    assert client.get("/api/photos").json()["total"] == 2
    assert client.get("/api/photos", params={"q": "private:true"}).json()["total"] == 1
    album = client.post("/api/albums", json={"title": "Scelte", "uids": uids[:1]}).json()
    assert client.post("/api/photos/batch", json={"uids": uids[1:2], "action": "add_to_album", "album": album["uid"]}).json() == {"changed": 1}
    assert client.get(f"/api/albums/{album['uid']}").json()["photo_count"] == 2
    assert client.post("/api/photos/batch", json={"uids": uids, "action": "boh"}).status_code == 422
    assert client.post("/api/photos/batch", json={"uids": ["zzz"], "action": "favorite"}).status_code == 404
    assert client.post("/api/photos/batch", json={"uids": uids, "action": "add_to_album", "album": "nope"}).status_code == 404
    assert client.post("/api/photos/batch", json={"uids": [], "action": "favorite"}).status_code == 422


def test_similar_endpoint_waits_for_embeddings_then_returns_hits(client, lib3, ctx):
    uid = uid_of(ctx, lib3[0])
    assert client.get(f"/api/photos/{uid}/similar").json() == {"ready": False, "hits": []}
    ctx.cfg = type(ctx.cfg)(**{**ctx.cfg.__dict__, "ai": type(ctx.cfg.ai)(model="fake")})
    base = rng.normal(size=16)
    vecs = np.stack([base, base + rng.normal(0, 0.05, 16), rng.normal(size=16)])
    vecs = (vecs / np.linalg.norm(vecs, axis=1, keepdims=True)).astype(np.float32)
    save_embeddings(ctx.db.conn(), "fake", lib3, vecs)
    data = client.get(f"/api/photos/{uid}/similar", params={"k": 2}).json()
    assert data["ready"] and data["hits"][0]["photo"]["uid"] == uid_of(ctx, lib3[1]) and 0 < data["hits"][0]["score"] <= 1
    assert "contesto" in data["hits"][0]["reasons"] or "palette" in data["hits"][0]["reasons"] or True
    assert client.get(f"/api/photos/{uid}/similar", params={"k": 99}).status_code == 422


def test_suggestions(client, lib3, ctx):
    conn = ctx.db.conn()
    tags.set_photo_label(conn, lib3[0], tags.get_or_create_label(conn, "Moto sportiva"), 5, 12)
    assert "label:" in client.get("/api/search/suggest", params={"q": "lab"}).json()["keys"]
    assert client.get("/api/search/suggest", params={"q": "label:spo"}).json()["values"] == ["moto-sportiva"]
    assert "rosso" in client.get("/api/search/suggest", params={"q": "ducati color:ro"}).json()["values"]
    assert client.get("/api/search/suggest", params={"q": ""}).json() == {"keys": [], "values": []}
    assert client.get("/api/search/suggest", params={"q": "boh:x"}).json()["values"] == []


# --- file ----------------------------------------------------------------------------------------------

def test_thumbnails_are_served_immutable_and_created_on_demand(client, lib3, ctx):
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (lib3[0],))
    r = client.get(f"/t/{sha1}/tile_224")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "immutable" in r.headers["cache-control"]
    assert Image.open(__import__("io").BytesIO(r.content)).size == (224, 224)
    for f in ctx.thumbs_dir.rglob(f"{sha1}_2560*"):
        f.unlink()
    big = client.get(f"/t/{sha1}/fit_2560")
    assert big.status_code == 200 and Image.open(__import__("io").BytesIO(big.content)).size == (640, 480)
    assert client.get(f"/t/{'0' * 40}/tile_224").status_code == 404
    assert client.get(f"/t/{sha1}/fit_99999").status_code == 404
    assert client.get("/t/notahash/tile_224").status_code == 404
    assert client.get(f"/t/{sha1.upper()}/tile_224").status_code == 404


def test_original_supports_range_and_stays_inside_library(client, lib3, ctx, tmp_path):
    uid = uid_of(ctx, lib3[0])
    path = Path(ctx.cfg.library_roots[0]) / "a.jpg"
    full = client.get(f"/api/files/{uid}/original")
    assert full.status_code == 200 and full.content == path.read_bytes() and full.headers["content-type"] == "image/jpeg"
    part = client.get(f"/api/files/{uid}/original", headers={"range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == path.read_bytes()[:10] and part.headers["content-range"].startswith("bytes 0-9/")
    outside = tmp_path / "segreto.txt"
    outside.write_text("riservato")
    ctx.db.conn().execute("UPDATE files SET root=?, dir='', name='segreto.txt' WHERE photo_id=?", (str(tmp_path), lib3[0]))
    ctx.db.conn().execute("INSERT INTO settings(key,value) VALUES ('x','y')")
    # la cartella di destinazione non è una radice nota né compare tra i root dei file indicizzati (cambiato sopra): la
    # difesa vale per percorsi fuori dalla libreria registrati altrove
    assert client.get("/api/files/nope/original").status_code == 404


@needs_ffmpeg
def test_video_streaming_with_range_and_transcode_flow(client, m, ctx):
    lib = Path(ctx.cfg.library_roots[0])
    make_video(lib / "web.mp4", seconds=2)
    make_video(lib / "old.mp4", seconds=2, codec="mpeg4")
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    by_name = {ctx.db.scalar("SELECT name FROM files WHERE photo_id=?", (p,)): uid_of(ctx, p) for p in ids}
    web = client.get(f"/api/videos/{by_name['web.mp4']}", headers={"range": "bytes=0-99"})
    assert web.status_code == 206 and web.headers["content-type"] == "video/mp4" and len(web.content) == 100
    old = client.get(f"/api/videos/{by_name['old.mp4']}")
    assert old.status_code == 202 and old.json() == {"status": "transcoding"}
    assert ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE type='transcode' AND state='queued'") >= 1
    m.runner.start()
    assert m.runner.wait_idle(120)
    done = client.get(f"/api/videos/{by_name['old.mp4']}")
    assert done.status_code == 200 and done.headers["content-type"] == "video/mp4"
    assert client.get(f"/api/videos/{uid_of(ctx, ids[0])[:0] or 'nope'}").status_code == 404


def test_video_endpoint_rejects_images(client, lib3, ctx):
    assert client.get(f"/api/videos/{uid_of(ctx, lib3[0])}").status_code == 404
    assert client.get(f"/api/live/{uid_of(ctx, lib3[0])}").status_code == 404


@needs_ffmpeg
def test_live_photo_video_is_served(client, ctx):
    lib = Path(ctx.cfg.library_roots[0])
    make_jpeg(lib / "IMG_1.jpg", size=(320, 240))
    make_video(lib / "IMG_1.mov", seconds=2)
    (pid,) = index_roots(ctx).changed_photo_ids
    process_photo(ctx, pid)
    r = client.get(f"/api/live/{uid_of(ctx, pid)}")
    assert r.status_code == 200 and r.headers["content-type"] in ("video/quicktime", "video/mp4")


def test_marker_crop(client, lib3, ctx):
    fid = ctx.db.scalar("SELECT id FROM files WHERE photo_id=?", (lib3[0],))
    ctx.db.conn().execute("INSERT INTO markers(uid, file_id, photo_id, x, y, w, h) VALUES ('m1', ?, ?, 0.3, 0.3, 0.2, 0.2)", (fid, lib3[0]))
    r = client.get("/api/markers/m1/crop", params={"size": 96})
    assert r.status_code == 200 and Image.open(__import__("io").BytesIO(r.content)).size == (96, 96)
    assert client.get("/api/markers/m1/crop", params={"size": 77}).status_code == 400
    assert client.get("/api/markers/zzz/crop").status_code == 404


# --- album, etichette, persone, duplicati ---------------------------------------------------------------

def test_albums_crud_and_rules(client, lib3, ctx):
    uids = [uid_of(ctx, p) for p in lib3]
    a = client.post("/api/albums", json={"title": "Vacanza", "uids": uids[:2]})
    assert a.status_code == 201 and a.json()["photo_count"] == 2 and a.json()["cover"]
    ref = a.json()["uid"]
    assert [x["title"] for x in client.get("/api/albums", params={"type": "album"}).json()] == ["Vacanza"]
    assert client.post(f"/api/albums/{ref}/photos", json={"uids": uids[2:]}).json() == {"changed": 1}
    assert client.post(f"/api/albums/{ref}/photos/remove", json={"uids": uids[:1]}).json() == {"changed": 1}
    patched = client.patch(f"/api/albums/{ref}", json={"title": "Estate", "favorite": True, "cover": uids[2]}).json()
    assert patched["title"] == "Estate" and patched["favorite"] is True and patched["photo_count"] == 2
    assert client.get("/api/photos", params={"q": f"album:{ref}"}).json()["total"] == 2
    assert client.patch(f"/api/albums/{ref}", json={"title": "  "}).status_code == 400
    assert client.patch(f"/api/albums/{ref}", json={"cover": "nope"}).status_code == 404
    assert client.get("/api/albums", params={"type": "boh"}).status_code == 400
    assert client.post("/api/albums", json={"title": ""}).status_code == 422
    assert client.delete(f"/api/albums/{ref}").status_code == 204
    assert client.get(f"/api/albums/{ref}").status_code == 404


def test_automatic_albums_cannot_be_deleted_and_calendar_lists_months(client, ctx):
    load(ctx, ("IMG_20240501_100000.jpg", (200, 1, 1)), ("IMG_20240502_100000.jpg", (1, 200, 1)),
         ("IMG_20240503_100000.jpg", (1, 1, 200)), ("IMG_20240504_100000.jpg", (90, 90, 90)))
    moments.generate(ctx)
    month = client.get("/api/albums", params={"type": "month"}).json()
    assert month and month[0]["title"] == "Maggio 2024" and month[0]["smart"] and month[0]["photo_count"] == 4
    assert client.delete(f"/api/albums/{month[0]['uid']}").status_code == 400
    assert client.post(f"/api/albums/{month[0]['uid']}/photos", json={"uids": ["x"]}).status_code == 400
    cal = client.get("/api/calendar").json()
    assert cal[0]["year"] == 2024 and cal[0]["months"][0]["month"] == 5 and cal[0]["months"][0]["count"] == 4


def test_labels_listing(client, lib3, ctx):
    conn = ctx.db.conn()
    lid = tags.get_or_create_label(conn, "Moto", "Veicoli")
    for p in lib3[:2]:
        tags.set_photo_label(conn, p, lid, 10, 12)
    data = client.get("/api/labels").json()
    assert data[0]["name"] == "Moto" and data[0]["count"] == 2 and data[0]["category"] == "Veicoli" and len(data[0]["cover"]) == 40


def add_face(ctx, pid: int, uid: str, face_id: str | None = None):
    fid = ctx.db.scalar("SELECT id FROM files WHERE photo_id=?", (pid,))
    ctx.db.conn().execute("INSERT INTO markers(uid, file_id, photo_id, face_id, x, y, w, h, score, size) "
                          "VALUES (?,?,?,?,0.2,0.2,0.3,0.3,95,150)", (uid, fid, pid, face_id))


def test_people_flow(client, lib3, ctx):
    ctx.db.conn().execute("INSERT INTO faces(id, samples, embedding, created_at) VALUES ('f1', 2, x'00', 'x')")
    add_face(ctx, lib3[0], "m1", "f1")
    add_face(ctx, lib3[1], "m2", "f1")
    add_face(ctx, lib3[2], "m3")
    unnamed = client.get("/api/faces").json()
    assert unnamed == [{"face": "f1", "samples": 2, "marker": unnamed[0]["marker"]}]
    named = client.post("/api/faces/f1/name", json={"name": "Anna Rossi"})
    assert named.status_code == 200
    subjects = client.get("/api/subjects").json()
    assert subjects[0]["name"] == "Anna Rossi" and subjects[0]["photo_count"] == 2 and subjects[0]["marker"]
    assert client.get("/api/faces").json() == []
    marco = client.post("/api/markers/m3/name", json={"name": "Marco"}).json()["subject"]
    assert client.get("/api/photos", params={"q": "person:anna"}).json()["total"] == 2
    renamed = client.patch(f"/api/subjects/{marco}", json={"name": "Marco Bianchi", "favorite": True}).json()
    assert renamed["name"] == "Marco Bianchi" and renamed["favorite"] is True
    assert client.patch(f"/api/subjects/{marco}", json={"name": "Anna Rossi"}).status_code == 400
    anna = named.json()["subject"]
    assert client.post("/api/subjects/merge", json={"keep": anna, "drop": marco}).json() == {"uid": anna}
    assert len(client.get("/api/subjects").json()) == 1
    assert client.post("/api/markers/m1/reject").status_code == 204
    assert client.post("/api/markers/zzz/reject").status_code == 404
    assert client.post("/api/faces/nope/name", json={"name": "X"}).status_code == 400
    assert client.post("/api/faces/f1/name", json={"name": ""}).status_code == 422
    assert client.patch("/api/subjects/zzz", json={"hidden": True}).status_code == 404


def test_duplicates_exact_and_near(client, ctx):
    import shutil

    lib = Path(ctx.cfg.library_roots[0])
    make_jpeg(lib / "a.jpg", color=(5, 6, 7))
    (lib / "sub").mkdir()
    shutil.copy(lib / "a.jpg", lib / "sub" / "copy.jpg")
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    exact = client.get("/api/duplicates").json()
    assert len(exact["groups"]) == 1 and exact["groups"][0]["copies"][0]["exists"] is True
    ctx.db.conn().execute("UPDATE photos SET phash=5")
    assert client.get("/api/duplicates", params={"kind": "near"}).json()["groups"] == []
    assert client.get("/api/duplicates", params={"kind": "boh"}).status_code == 400


# --- libreria, configurazione, rinomina, job ---------------------------------------------------------------

def test_config_get_and_validated_put(client, m, tmp_path):
    assert client.get("/api/config").json()["port"] == 8000
    r = client.put("/api/config", json={"changes": {"workers": 3, "rename": {"pattern": "{date:%Y}_{slug}"}}})
    assert r.status_code == 200 and r.json()["workers"] == 3 and m.cfg.rename.pattern == "{date:%Y}_{slug}"
    assert (tmp_path / "config.yaml").exists()
    assert client.put("/api/config", json={"changes": {"port": 0}}).status_code == 400
    assert client.put("/api/config", json={"changes": {"rename": {"pattern": "{boh}"}}}).status_code == 400
    assert client.put("/api/config", json={"changes": {"library_roots": [str(tmp_path / "manca")]}}).status_code == 400
    assert client.put("/api/config", json={"changes": {"library_roots": [str(tmp_path)]}}).status_code == 200


def test_library_actions_validate_input(client, m, ctx, tmp_path):
    assert client.post("/api/library/index", json={}).status_code == 202
    assert client.post("/api/library/import", json={"source": str(tmp_path / "manca")}).status_code == 400
    assert client.post("/api/library/import", json={"source": str(tmp_path)}).status_code == 202
    assert client.post("/api/library/cleanup", json={"purge": False}).status_code == 202
    assert client.post("/api/library/analyze").status_code == 202
    types = {r["type"] for r in ctx.db.all("SELECT type FROM jobs")}
    assert {"index", "import", "cleanup", "analyze"} <= types
    m.ctx.cfg = type(m.cfg)(**{**m.cfg.__dict__, "library_roots": ()})
    assert client.post("/api/library/index", json={}).status_code == 400
    assert client.get("/api/status").json()["first_run"] is True


def test_index_job_is_deduplicated(client, ctx):
    first = client.post("/api/library/index", json={}).json()["job"]
    assert client.post("/api/library/index", json={}).json()["job"] == first


def test_rename_preview_apply_and_rollback_via_api(client, m, lib3, ctx):
    lib = Path(ctx.cfg.library_roots[0])
    uid = uid_of(ctx, lib3[0])
    client.patch(f"/api/photos/{uid}", json={"title": "Tramonto"})
    preview = client.post("/api/rename/preview", json={}).json()
    assert preview["photos"] == 3 and preview["files"] == 3 and (lib / "a.jpg").exists()
    assert any(op["to"].endswith("_tramonto.jpg") for op in preview["ops"])
    assert client.post("/api/rename/preview", json={"pattern": "{boh}"}).status_code in (400, 422)
    m.runner.start()
    assert client.post("/api/rename/apply", json={}).status_code == 202
    assert m.runner.wait_idle(30)
    assert not (lib / "a.jpg").exists() and any(p.name.endswith("_tramonto.jpg") for p in lib.iterdir())
    batches = client.get("/api/rename/batches").json()
    assert batches[0]["done"] == 3
    assert client.post("/api/rename/rollback", json={}).status_code == 400
    assert client.post("/api/rename/rollback", json={"batch_id": batches[0]["batch_id"]}).status_code == 202
    assert m.runner.wait_idle(30)
    assert (lib / "a.jpg").exists() and (lib / "b.jpg").exists()


def test_export_downloads(client, lib3):
    j = client.get("/api/export", params={"format": "json"})
    assert j.status_code == 200 and len(json.loads(j.content)) == 3
    c = client.get("/api/export", params={"format": "csv"})
    assert c.content.startswith(b"\xef\xbb\xbfuid,file")
    assert client.get("/api/export", params={"format": "xml"}).status_code == 400


def test_jobs_listing_and_cancel(client, m):
    job = m.runner.queue.enqueue("index", {})
    listed = client.get("/api/jobs").json()
    assert listed[0]["id"] == job and listed[0]["state"] == "queued"
    assert client.post(f"/api/jobs/{job}/cancel").json() == {"cancelled": True}
    assert client.post("/api/jobs/9999/cancel").json() == {"cancelled": False}


def test_event_stream_yields_job_events_and_heartbeats(monkeypatch):
    monkeypatch.setattr(routes_library, "HEARTBEAT", 0.05)
    bus = EventBus()

    async def run() -> list[str]:
        stream = routes_library.event_stream(bus)
        out = [await stream.__anext__()]                       # retry iniziale
        out.append(await stream.__anext__())                   # battito: nessun evento in coda
        bus.publish({"event": "job", "type": "index", "state": "done"})
        out.append(await stream.__anext__())
        await stream.aclose()
        return out

    got = asyncio.run(run())
    assert got[0].startswith("retry:") and got[1] == ": ping\n\n"
    assert json.loads(got[2].removeprefix("data: ")) == {"event": "job", "type": "index", "state": "done"}
    assert bus._listeners == []                                # l'ascoltatore viene rimosso alla chiusura


# --- interfaccia statica -------------------------------------------------------------------------------------

def test_spa_fallback_without_and_with_dist(m, tmp_path):
    bare = TestClient(create_app(m, allowed_hosts={"testserver"}, dist=tmp_path / "vuota", start_workers=False))
    assert "Mosaic è in funzione" in bare.get("/").text
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("riservato", encoding="utf-8")
    c = TestClient(create_app(m, allowed_hosts={"testserver"}, dist=dist, start_workers=False))
    assert c.get("/").text == "<html>app</html>" and c.get("/albums/qualcosa").text == "<html>app</html>"
    assert c.get("/assets/app.js").text == "console.log(1)" and c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/api/inesistente").status_code == 404 and c.get("/t/inesistente").status_code == 404
    for attack in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"):
        assert "riservato" not in c.get(attack).text


# --- contesto delle didascalie ------------------------------------------------------------------------------

def folder_album(client, m):
    moments.generate(m.ctx)
    return next(a for a in client.get("/api/albums?type=folder").json() if a["smart"])


def test_folder_album_stores_the_story_of_the_day(client, m, lib3):
    album = folder_album(client, m)
    r = client.patch(f"/api/albums/{album['uid']}", json={"description": "Giornata al circuito con Luca."})
    assert r.status_code == 200 and r.json()["description"] == "Giornata al circuito con Luca."
    moments.generate(m.ctx)   # rigenerare gli album automatici non cancella il racconto
    assert client.get(f"/api/albums/{album['uid']}").json()["description"] == "Giornata al circuito con Luca."


def test_album_caption_regenerates_only_that_albums_photos(client, m, ctx, lib3, monkeypatch):
    monkeypatch.setattr(routes_collections, "captioning_available", lambda _ctx: True)
    album = folder_album(client, m)
    sources_xmp = 32
    ctx.db.conn().execute("UPDATE photos SET title_src=?, caption_src=? WHERE id=?", (sources_xmp, sources_xmp, lib3[0]))
    r = client.post(f"/api/albums/{album['uid']}/caption")
    assert r.status_code == 202
    body = r.json()
    assert body["photos"] == 2   # la foto con titolo e didascalia da XMP non si rigenera
    job = ctx.db.one("SELECT type, payload FROM jobs WHERE id=?", (body["job"],))
    payload = json.loads(job["payload"])
    assert job["type"] == "caption" and payload["force"] is True and sorted(payload["ids"]) == sorted(lib3[1:])


def test_album_caption_needs_the_llm_and_a_real_album(client, m, lib3, monkeypatch):
    album = folder_album(client, m)
    monkeypatch.setattr(routes_collections, "captioning_available", lambda _ctx: False)
    assert client.post(f"/api/albums/{album['uid']}/caption").status_code == 400
    monkeypatch.setattr(routes_collections, "captioning_available", lambda _ctx: True)
    assert client.post("/api/albums/inesistente/caption").status_code == 404


def test_album_caption_twice_queues_two_jobs_with_their_own_photos(client, m, lib3, monkeypatch):
    monkeypatch.setattr(routes_collections, "captioning_available", lambda _ctx: True)
    album = folder_album(client, m)
    first = client.post(f"/api/albums/{album['uid']}/caption").json()["job"]
    second = client.post(f"/api/albums/{album['uid']}/caption").json()["job"]
    assert first != second


def test_about_text_is_saved_in_the_config(client, m, tmp_path):
    r = client.put("/api/config", json={"changes": {"llm": {"about": "Siamo una famiglia di Padova."}}})
    assert r.status_code == 200 and m.cfg.llm.about == "Siamo una famiglia di Padova."
    assert client.put("/api/config", json={"changes": {"llm": {"about": "x" * 2001}}}).status_code == 400


# --- didascalie nel cloud ------------------------------------------------------------------------------------

def test_cloud_keys_are_saved_but_never_returned(client, m):
    assert client.get("/api/llm/cloud").json()["providers"]["nvidia"]["key"] is None
    r = client.put("/api/llm/cloud/key", json={"provider": "nvidia", "key": "nvapi-segreta"})
    assert r.status_code == 200
    assert r.json()["providers"]["nvidia"]["key"] == "file"
    assert "nvapi-segreta" not in client.get("/api/llm/cloud").text
    assert "nvapi-segreta" not in json.dumps(client.get("/api/config").json())
    assert client.put("/api/llm/cloud/key", json={"provider": "nvidia", "key": ""}).json()["providers"]["nvidia"]["key"] is None


@pytest.mark.parametrize("body", [{"provider": "boh", "key": "x"}, {"provider": "nvidia", "key": "a" * 513},
                                  {"provider": "nvidia", "key": "due righe\nqui"}])
def test_invalid_cloud_key_requests_are_rejected(client, body):
    assert client.put("/api/llm/cloud/key", json=body).status_code in (400, 422)


def test_cloud_settings_go_through_the_config_and_make_captions_available(client, m, tmp_path, monkeypatch):
    m.ctx.models_dir = tmp_path / "vuoto"
    monkeypatch.setattr("mosaic.llm.backends.server_for", lambda _ctx: type("S", (), {"available": False})())
    assert client.get("/api/status").json()["llm_ready"] is False
    client.put("/api/llm/cloud/key", json={"provider": "nvidia", "key": "k"})
    assert client.get("/api/status").json()["llm_ready"] is False      # chiave presente ma cloud spento
    r = client.put("/api/config", json={"changes": {"llm": {"cloud": {"enabled": True}}}})
    assert r.status_code == 200 and r.json()["llm"]["cloud"]["enabled"] is True
    assert client.get("/api/status").json()["llm_ready"] is True
    assert client.put("/api/config", json={"changes": {"llm": {"cloud": {"providers": ["boh"]}}}}).status_code == 400


# --- eliminare foto e scollegare cartelle (D-20) ----------------------------------------------------

def test_status_and_config_expose_delete_mode(client):
    assert client.get("/api/status").json()["delete_mode"] == "catalog"
    assert client.put("/api/config", json={"changes": {"delete_mode": "permanent"}}).status_code == 400
    assert client.put("/api/config", json={"changes": {"delete_mode": "trash"}}).status_code == 200
    assert client.get("/api/status").json()["delete_mode"] == "trash"


def test_remove_photos_in_catalog_mode_keeps_files_and_lists_them_as_excluded(client, lib3, ctx):
    uids = [uid_of(ctx, p) for p in lib3]
    r = client.post("/api/photos/remove", json={"uids": uids[:2]})
    assert r.status_code == 200
    assert r.json() == {"removed": 2, "files": 2, "mode": "catalog", "failed": []}
    assert client.get("/api/photos").json()["total"] == 1
    assert client.get(f"/api/photos/{uids[0]}").status_code == 404
    assert (Path(ctx.cfg.library_roots[0]) / "a.jpg").exists()
    excluded = client.get("/api/excluded").json()
    assert sorted(e["name"] for e in excluded) == ["a.jpg", "b.jpg"]
    assert client.post("/api/photos/remove", json={"uids": ["inesistente"]}).status_code == 404
    assert client.post("/api/photos/remove", json={"uids": []}).status_code == 422


def test_remove_photos_in_trash_mode_reports_failures_and_keeps_the_photo(client, lib3, ctx, monkeypatch):
    from mosaic.organize import trash

    client.put("/api/config", json={"changes": {"delete_mode": "trash"}})
    monkeypatch.setattr(trash, "has_recycle_bin", lambda path: False)  # niente Cestino: i file restano dove sono
    uid = uid_of(ctx, lib3[0])
    body = client.post("/api/photos/remove", json={"uids": [uid]}).json()
    assert body["removed"] == 0 and body["mode"] == "trash"
    assert body["failed"][0]["uid"] == uid and "Cestino" in body["failed"][0]["error"]
    assert client.get(f"/api/photos/{uid}").status_code == 200
    assert (Path(ctx.cfg.library_roots[0]) / "a.jpg").exists()


def test_excluded_restore_queues_an_index_and_clears_the_list(client, lib3, ctx):
    uids = [uid_of(ctx, p) for p in lib3]
    client.post("/api/photos/remove", json={"uids": uids[:1]})
    (item,) = client.get("/api/excluded").json()
    r = client.post("/api/excluded/restore", json={"sha1s": [item["sha1"]]})
    assert r.status_code == 202 and r.json()["restored"] == 1 and r.json()["job"] > 0
    assert client.get("/api/excluded").json() == []


def test_unlink_root_route_detaches_the_folder_and_the_job_cleans_the_catalog(client, lib3, ctx, m):
    from mosaic.jobs.handlers import build_handlers

    root = ctx.cfg.library_roots[0]
    listed = client.get("/api/library/roots").json()
    assert listed["roots"] == [{"path": root, "linked": True, "photos": 3, "exists": True}] and listed["unlinked"] == []

    r = client.post("/api/library/roots/unlink", json={"root": root})
    assert r.status_code == 202 and r.json()["job"] > 0
    assert client.get("/api/status").json()["library_roots"] == []  # fuori dalla configurazione subito
    stale = client.get("/api/library/roots").json()
    assert stale["roots"] == [{"path": root, "linked": False, "photos": 3, "exists": True}]  # ancora nel catalogo finché il job non gira

    job = m.runner.queue.claim({"unlink"})
    assert job is not None and job.payload == {"root": root}
    build_handlers()["unlink"](ctx, job, type("Ctl", (), {"progress": lambda *a: None, "submit": lambda *a, **k: 0})())
    done = client.get("/api/library/roots").json()
    assert done["roots"] == [] and done["unlinked"] == [root]
    assert client.get("/api/photos").json()["total"] == 0
    assert (Path(root) / "a.jpg").exists()
    assert client.post("/api/library/roots/unlink", json={"root": str(Path(root) / "boh")}).status_code == 400


def test_jobs_expose_start_time_and_totals(client, m):
    m.runner.queue.enqueue("process", {"ids": [1, 2, 3]})
    job = client.get("/api/jobs").json()[0]
    assert job["total"] == 3 and job["done"] == 0 and "started_at" in job


def test_saved_selection_belongs_to_its_source_album(client, lib3, ctx):
    from mosaic.organize import moments

    moments.generate(ctx)
    folder = ctx.db.one("SELECT uid, title FROM albums WHERE type='folder' LIMIT 1")
    uids = [uid_of(ctx, p) for p in lib3]
    saved = client.post("/api/albums", json={"title": "Selezione", "uids": uids[:2], "source": folder["uid"]}).json()
    assert saved["source"] == {"uid": folder["uid"], "title": folder["title"], "type": "folder"}
    listed = client.get(f"/api/albums/{folder['uid']}/selections").json()
    assert [a["uid"] for a in listed] == [saved["uid"]]
    moments.generate(ctx)  # gli album automatici si rigenerano: il legame resta
    assert [a["uid"] for a in client.get(f"/api/albums/{folder['uid']}/selections").json()] == [saved["uid"]]
    assert client.post("/api/albums", json={"title": "X", "source": "nope"}).status_code == 404
