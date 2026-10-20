"""«Crea selezione» (D-21): misure visive, scelta varia e API."""

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter

from mosaic.ai.vectors import save_embeddings
from mosaic.api.app import create_app
from mosaic.db import utcnow
from mosaic.index.indexer import index_roots
from mosaic.organize import aesthetics, albums, curate
from mosaic.pipeline import process_photo
from mosaic.service import Mosaic

rng = np.random.default_rng(7)


def texture(size=(640, 480), seed=0, blur=0.0, gain=1.0) -> Image.Image:
    noise = np.random.default_rng(seed).integers(0, 256, (size[1] // 8, size[0] // 8, 3), dtype=np.uint8)
    img = Image.fromarray(noise).resize(size, Image.NEAREST)  # blocchi netti: bordi forti come in una foto a fuoco
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if gain != 1.0:
        img = Image.fromarray(np.clip(np.asarray(img, dtype=np.float32) * gain, 0, 255).astype(np.uint8))
    return img


def save(lib: Path, name: str, img: Image.Image) -> None:
    lib.mkdir(parents=True, exist_ok=True)
    img.save(lib / name, "JPEG", quality=92)


def index_all(ctx) -> dict[str, int]:
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    rows = ctx.db.all("SELECT p.id, f.name FROM photos p JOIN files f ON f.photo_id=p.id AND f.is_primary=1")
    return {r["name"]: r["id"] for r in rows}


def album_of(ctx, ids) -> str:
    with ctx.db.tx() as conn:
        album = albums.create_album(conn, "Festa")
        albums.add_photos(conn, album["id"], ids)
    return album["uid"]


# --- misure ---------------------------------------------------------------------------------------

def test_sharp_photo_measures_sharper_than_blurred_one():
    sharp = aesthetics.measure_image(texture(seed=1))
    blurred = aesthetics.measure_image(texture(seed=1, blur=6))
    assert sharp.sharpness > blurred.sharpness + 2


def test_bokeh_background_does_not_hide_a_sharp_subject():
    img = texture(seed=2, blur=8)
    img.paste(texture((160, 120), seed=3), (240, 180))  # soggetto a fuoco al centro, sfondo sfocato
    assert aesthetics.measure_image(img).sharpness > aesthetics.measure_image(texture(seed=2, blur=8)).sharpness + 2


def test_dark_and_flat_photos_have_poor_exposure():
    good = aesthetics.measure_image(texture(seed=4)).exposure
    dark = aesthetics.measure_image(texture(seed=4, gain=0.05)).exposure
    flat = aesthetics.measure_image(Image.new("RGB", (200, 200), (128, 128, 128))).exposure
    assert good > 0.9 and dark < 0.4 and flat < 0.6


def test_measures_are_cached_and_redone_when_the_file_changes(ctx, library):
    save(library, "a.jpg", texture(seed=5))
    pid = index_all(ctx)["a.jpg"]
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    first = aesthetics.measures(ctx, [(pid, sha1)])
    assert pid in first and ctx.db.scalar("SELECT COUNT(*) FROM photo_aesthetics") == 1
    ctx.db.conn().execute("UPDATE photo_aesthetics SET sharpness=99")
    assert aesthetics.measures(ctx, [(pid, sha1)])[pid].sharpness == 99  # dalla cache
    ctx.db.conn().execute("UPDATE photo_aesthetics SET sha1='altro'")
    assert aesthetics.measures(ctx, [(pid, sha1)])[pid].sharpness == pytest.approx(first[pid].sharpness)
    assert aesthetics.measures(ctx, [(pid, None)]) == {}


def test_missing_thumbnail_gives_no_measure(ctx):
    assert aesthetics.measure_file(Path("non-esiste.jpg")) is None
    assert aesthetics.measures(ctx, [(123, "f" * 40)]) == {}


# --- scelta su un insieme sintetico -----------------------------------------------------------------

def pool(n: int, *, vectors=None, people=None, seconds=None, names=None) -> curate.Pool:
    vecs = vectors if vectors is not None else np.eye(n, 16, dtype=np.float32)
    return curate.Pool(
        rows=[{"id": i + 1, "favorite": 0, "phash": None, "taken_at_local": None} for i in range(n)], relevance=None,
        seconds=np.array(seconds if seconds is not None else [i * 600.0 for i in range(n)]),
        people=[frozenset(p) for p in (people or [()] * n)], names=names or {},
        face_area=np.zeros(n, np.float32), vectors=vecs, has_vec=np.ones(n, bool))


def test_near_duplicates_are_skipped_in_favour_of_different_photos():
    vecs = np.eye(6, 16, dtype=np.float32)
    vecs[1] = vecs[0] * 0.99 + vecs[5] * 0.14  # 1 è una raffica di 0
    vecs[1] /= np.linalg.norm(vecs[1])
    score = np.array([0.9, 0.95, 0.5, 0.5, 0.5, 0.5], dtype=np.float32)
    picks = curate.select(pool(6, vectors=vecs), score, curate.Options(count=4))
    ids = [p.id for p in picks]
    assert 2 in ids and 1 not in ids  # tenuta la migliore della raffica
    assert any("scatti simili" in r for p in picks for r in p.reasons)


def test_selection_spreads_over_the_whole_event():
    # 20 foto tutte uguali per qualità nella prima ora, 4 alla fine della giornata
    seconds = [i * 60.0 for i in range(20)] + [36000.0 + i * 60 for i in range(4)]
    score = np.array([0.8] * 20 + [0.6] * 4, dtype=np.float32)
    picks = curate.select(pool(24, seconds=seconds, vectors=np.tile(np.eye(1, 16, dtype=np.float32), (24, 1)) * 0 +
                               np.eye(24, 16, dtype=np.float32)), score, curate.Options(count=6))
    assert any(p.id > 20 for p in picks)


def test_people_option_favours_new_faces():
    people = [("s1",), ("s1",), ("s1",), ("s2",), ("s3",), ()]
    score = np.array([0.9, 0.9, 0.9, 0.6, 0.6, 0.9], dtype=np.float32)
    names = {"s1": "Anna", "s2": "Luca", "s3": "Marta"}
    picks = curate.select(pool(6, people=people, names=names, seconds=[0.0] * 6), score,
                          curate.Options(count=3, mode="variety"))
    chosen = {p.id for p in picks}
    assert {4, 5} <= chosen
    assert any(r.startswith("Prima foto con") for p in picks for r in p.reasons)


def test_seed_changes_the_selection_but_zero_is_stable():
    score = np.full(40, 0.5, dtype=np.float32)
    base = pool(40, vectors=np.zeros((40, 16), np.float32), seconds=[0.0] * 40)
    base.has_vec[:] = False
    a = [p.id for p in curate.select(base, score, curate.Options(count=5))]
    assert a == [p.id for p in curate.select(base, score, curate.Options(count=5))]
    assert any([p.id for p in curate.select(base, score, curate.Options(count=5, seed=s))] != a for s in range(1, 6))


def test_options_are_validated():
    for bad in ({"count": 0}, {"count": 500}, {"mode": "boh"}, {"shape": "tondo"}):
        with pytest.raises(curate.CurateError):
            curate.Options(**bad)
    assert curate.filter_query("A1", curate.Options(shape="4:3")) == "album:A1 ratio:4:3 photo:true"
    assert curate.filter_query("A1", curate.Options(shape="portrait", videos=True, text="palco")) == \
        "album:A1 portrait:true palco"


def test_rank01_handles_nan_and_single_value():
    out = curate.rank01(np.array([3.0, np.nan, 1.0]))
    assert out[0] == 1.0 and out[2] == 0.0 and np.isnan(out[1])
    assert curate.rank01(np.array([5.0]))[0] == 1.0


# --- insieme, su una libreria vera --------------------------------------------------------------------

@pytest.fixture()
def event(ctx, library):
    for h in range(8):
        save(library, f"IMG_20240501_{10 + h:02d}0000.jpg", texture(seed=10 + h))
    save(library, "IMG_20240501_103000.jpg", texture(seed=30, blur=7))       # mossa
    save(library, "IMG_20240501_113000.jpg", texture(seed=31, gain=0.04))    # buia
    save(library, "IMG_20240501_120000_v.jpg", texture((480, 640), seed=32))  # verticale
    names = index_all(ctx)
    ids = sorted(names.values())
    save_embeddings(ctx.db.conn(), ctx.cfg.ai.model, ids,
                    np.stack([v / np.linalg.norm(v) for v in rng.normal(size=(len(ids), 768))]).astype(np.float32))
    return names, album_of(ctx, ids)


def test_curate_drops_badly_taken_photos_and_orders_by_time(ctx, event):
    names, uid = event
    result = curate.curate(ctx, uid, curate.Options(count=4))
    ids = [p.id for p in result.picks]
    assert len(ids) == 4
    assert names["IMG_20240501_103000.jpg"] not in ids and names["IMG_20240501_113000.jpg"] not in ids
    assert result.stats["rejected"] >= 2 and result.stats["candidates"] == 11
    taken = [ctx.db.scalar("SELECT taken_at FROM photos WHERE id=?", (i,)) for i in ids]
    assert taken == sorted(taken)
    assert sum(b[1] for b in result.stats["timeline"]) == 4


def test_curate_respects_the_format(ctx, event):
    names, uid = event
    result = curate.curate(ctx, uid, curate.Options(count=5, shape="portrait"))
    assert [p.id for p in result.picks] == [names["IMG_20240501_120000_v.jpg"]]


def test_curate_counts_people(ctx, event):
    names, uid = event
    now = utcnow()
    with ctx.db.tx() as conn:
        conn.execute("INSERT INTO subjects(uid, name, slug, created_at) VALUES ('j1','Giulia','giulia',?)", (now,))
        subj = conn.execute("SELECT id FROM subjects WHERE uid='j1'").fetchone()[0]
        pid = names["IMG_20240501_140000.jpg"]
        fid = conn.execute("SELECT id FROM files WHERE photo_id=?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO markers(uid, file_id, photo_id, subj_id, x, y, w, h) VALUES ('m1',?,?,?,0.4,0.4,0.3,0.3)",
                     (fid, pid, subj))
    result = curate.curate(ctx, uid, curate.Options(count=3))
    assert pid in [p.id for p in result.picks]
    assert result.stats["people"] == [{"name": "Giulia", "count": 1}] and result.stats["people_total"] == 1


def test_curate_endpoint(ctx, event, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    m = Mosaic(ctx)
    try:
        client = TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False))
        _, uid = event
        res = client.post(f"/api/albums/{uid}/curate", json={"count": 3, "mode": "quality"})
        assert res.status_code == 200
        body = res.json()
        assert len(body["photos"]) == 3 and body["album"]["uid"] == uid and "reasons" in body["photos"][0]
        assert body["stats"]["ai_ready"] is True
        assert client.post(f"/api/albums/{uid}/curate", json={"shape": "tondo"}).status_code == 422
        assert client.post(f"/api/albums/{uid}/curate", json={"count": 0}).status_code == 422
        assert client.post(f"/api/albums/{uid}/curate", json={"text": "year:abc"}).status_code == 400
        assert client.post("/api/albums/nope/curate", json={}).status_code == 404
        empty = client.post(f"/api/albums/{uid}/curate", json={"shape": "square"}).json()
        assert empty["photos"] == [] and empty["stats"]["candidates"] == 0
    finally:
        m.runner.stop()


def test_only_bursts_left_gives_fewer_photos_instead_of_repeats():
    vecs = np.tile(np.eye(1, 16, dtype=np.float32), (5, 1))  # cinque scatti della stessa raffica
    picks = curate.select(pool(5, vectors=vecs), np.full(5, 0.5, dtype=np.float32), curate.Options(count=3))
    assert len(picks) == 1 and "La migliore di 5 scatti simili" in picks[0].reasons
