"""Poster a mosaico stile Mondrian (D-26): impaginazione, ritagli, disegno, API e job."""

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg
from fastapi.testclient import TestClient
from PIL import Image

from mosaic.api.app import create_app
from mosaic.index.indexer import index_roots
from mosaic.organize import poster
from mosaic.pipeline import process_photo
from mosaic.service import Mosaic


def pieces(n=10, colors=2):
    rng = np.random.default_rng(1)
    out = [poster.Piece(weight=3.0 if i == 0 else 1.2, aspect=float(rng.choice([1.5, 0.667, 1.0])), photo_id=i) for i in range(n)]
    return out + [poster.Piece(weight=0.5, color=c) for c in poster.MONDRIAN[:colors]]


# --- impaginazione -------------------------------------------------------------------------------------

def test_layout_tiles_the_whole_sheet_with_every_piece_once():
    lay = poster.layout(pieces(), 297, 420, seed=5)
    assert sorted(c.piece.photo_id for c in lay.cells if c.piece.photo_id is not None) == list(range(10))
    assert sum(1 for c in lay.cells if c.piece.color) == 2
    assert sum(c.w * c.h for c in lay.cells) == pytest.approx(297 * 420)
    for a in lay.cells:
        assert a.x >= -1e-6 and a.y >= -1e-6 and a.x + a.w <= 297 + 1e-6 and a.y + a.h <= 420 + 1e-6
        for b in lay.cells:
            if a is not b:
                overlap_w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                overlap_h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                assert overlap_w <= 1e-6 or overlap_h <= 1e-6


def test_layout_crops_little_and_gives_more_space_to_the_best_photo():
    lay = poster.layout(pieces(16, 3), 297, 420, seed=2)
    photos = [c for c in lay.cells if c.piece.aspect]
    loss = [1 - min((c.w / c.h) / c.piece.aspect, c.piece.aspect / (c.w / c.h)) for c in photos]
    assert float(np.mean(loss)) < 0.2
    hero = next(c for c in photos if c.piece.photo_id == 0)
    assert hero.w * hero.h > np.median([c.w * c.h for c in photos])


def test_same_seed_same_layout_new_seed_new_layout():
    a = poster.layout(pieces(), 297, 420, seed=9)
    b = poster.layout(pieces(), 297, 420, seed=9)
    c = poster.layout(pieces(), 297, 420, seed=10)
    key = lambda lay: [(round(x.x, 3), round(x.y, 3), x.piece.photo_id) for x in lay.cells]  # noqa: E731
    assert key(a) == key(b) and key(a) != key(c)


def test_crop_follows_the_faces():
    # foto larga in un riquadro verticale: si taglia ai lati, tenendo il volto a destra
    x0, y0, x1, y1 = poster.crop_box(1.5, 0.75, (0.8, 0.5))
    assert (y0, y1) == (0.0, 1.0) and x1 == pytest.approx(1.0) and x1 - x0 == pytest.approx(0.5)
    x0, y0, x1, y1 = poster.crop_box(0.667, 1.5, (0.5, 0.2))
    assert (x0, x1) == (0.0, 1.0) and y0 == pytest.approx(0.0) and y1 == pytest.approx(0.667 / 1.5)


def test_options_and_sizes():
    with pytest.raises(poster.PosterError):
        poster.Options(paper="a0")
    with pytest.raises(poster.PosterError):
        poster.Options(style="cubista")
    assert poster.Options(paper="a3", landscape=True).size_mm == (420, 297)
    w, h, dpi = poster.pixel_size(poster.Options(paper="70x100", dpi=300))
    assert w * h <= poster.MAX_PIXELS and dpi < 300
    assert poster.pixel_size(poster.Options(paper="a4", dpi=200))[2] == 200
    assert poster.poster_name("Festa d'estate!", __import__("datetime").datetime(2026, 10, 20, 9, 5, 1)) == \
        "poster-festa-d-estate-20261020-090501.jpg"
    for bad in ("../x.jpg", "a.png", "x/../../y.jpg"):
        with pytest.raises(poster.PosterError):
            poster.poster_path(Path("x"), bad)


# --- disegno, API e job --------------------------------------------------------------------------------

@pytest.fixture()
def photos(ctx, library):
    colors = [(200, 30, 30), (30, 160, 60), (40, 60, 200), (220, 200, 40), (120, 120, 120)]
    for i, color in enumerate(colors):
        make_jpeg(library / f"p{i}.jpg", color=color, size=(900, 600) if i % 2 else (600, 900))
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    return [r["uid"] for r in ctx.db.all("SELECT uid FROM photos ORDER BY id")]


def test_render_draws_photos_colors_and_lines(ctx, photos, tmp_path):
    out = tmp_path / "poster.jpg"
    seen = []
    path, dpi = poster.render(ctx, photos, poster.Options(paper="a4", dpi=72, colors=2, line="bold"), out,
                              progress=lambda d, t: seen.append((d, t)))
    img = Image.open(path)
    assert img.size == poster.pixel_size(poster.Options(paper="a4", dpi=72))[:2] and dpi == 72
    assert seen[-1] == (7, 7)
    corner = img.getpixel((1, 1))
    assert max(corner) < 40  # la cornice nera di Mondrian
    colors = {img.getpixel((x, y)) for x in range(0, img.width, 7) for y in range(0, img.height, 7)}
    assert any(abs(c[0] - 0xd5) < 12 and c[1] < 60 for c in colors)  # c'è il rosso Mondrian


@pytest.fixture()
def client(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(poster, "posters_dir", lambda: tmp_path / "Immagini" / "Mosaic")
    m = Mosaic(ctx)
    yield TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False)), m
    m.runner.stop()


def test_layout_api_returns_cells_for_the_preview(client, photos):
    c, _ = client
    res = c.post("/api/posters/layout", json={"uids": photos, "paper": "a3", "colors": 2, "seed": 4})
    assert res.status_code == 200
    body = res.json()
    assert len(body["cells"]) == len(photos) + 2 and body["ink"] == poster.INK["mondrian"]
    assert {cell["uid"] for cell in body["cells"] if cell["uid"]} == set(photos)
    assert all(cell["hash"] for cell in body["cells"] if cell["uid"])
    assert sum(cell["w"] * cell["h"] for cell in body["cells"]) == pytest.approx(1.0)
    assert c.post("/api/posters/layout", json={"uids": photos, "paper": "a0"}).status_code == 422
    assert c.post("/api/posters/layout", json={"uids": ["nessuna"]}).status_code == 400


def test_create_runs_as_a_job_for_a_manual_album(client, photos, ctx):
    from mosaic.jobs.handlers import h_poster
    from mosaic.jobs.queue import Job
    from mosaic.organize import albums

    c, m = client
    with ctx.db.tx() as conn:
        album = albums.create_album(conn, "Festa")["uid"]
    res = c.post("/api/posters", json={"uids": photos[:3], "paper": "a4", "dpi": 72, "title": "Festa", "album": album})
    assert res.status_code == 202 and res.json()["name"].startswith("poster-festa-")
    assert res.json()["folder"] == str(poster.posters_dir() / "festa")
    row = m.runner.queue.get(res.json()["job"])
    assert row["type"] == "poster"

    class Ctl:
        messages: list[str] = []

        def progress(self, value, message="", *_counts):
            self.messages.append(message)

    ctl = Ctl()
    h_poster(ctx, Job(row["id"], "poster", json.loads(row["payload"])), ctl)
    assert "Poster salvato" in ctl.messages[-1]
    name = res.json()["name"]
    served = c.get(f"/api/albums/{album}/posters/{name}")
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert c.post(f"/api/albums/{album}/posters/..%2Fx.jpg/reveal").status_code in (400, 404, 405)  # mai 204
    assert c.post(f"/api/albums/{album}/posters/x.png/reveal").status_code == 400
    assert c.get("/api/albums/nope/posters").status_code == 404


# --- dati dell'evento nei riquadri (in negativo) -------------------------------------------------------

def test_dates_are_written_like_a_person_would():
    from datetime import datetime as dt

    from mosaic.organize.poster_text import format_dates
    assert format_dates(dt(2025, 9, 13, 21), dt(2025, 9, 13, 23)) == "13 settembre 2025"
    assert format_dates(dt(2025, 9, 13, 21), dt(2025, 9, 14, 4)) == "13–14 settembre 2025"
    assert format_dates(dt(2025, 9, 30), dt(2025, 10, 2)) == "30 settembre – 2 ottobre 2025"
    assert format_dates(dt(2024, 12, 31), dt(2025, 1, 1)) == "31 dicembre 2024 – 1 gennaio 2025"


def test_event_facts_use_what_the_user_wrote_first(ctx, photos):
    from mosaic.organize.poster_text import event_facts

    ids = [r[0] for r in ctx.db.conn().execute("SELECT id FROM photos")]
    facts = event_facts(ctx, ids, occasion="Festa al garage", place="Padova", date="estate 2025")
    assert [(f.label, f.value) for f in facts][:3] == [("", "Festa al garage"), ("Data", "estate 2025"), ("Luogo", "Padova")]
    assert facts[-1].label == "Foto" and facts[-1].value == str(len(ids))
    auto = event_facts(ctx, ids)
    assert auto[0].label == "Data" and all(f.value for f in auto)  # niente riquadri vuoti


def test_text_fits_inside_its_block():
    from mosaic.organize.poster_text import Fact, fit_text

    small = fit_text(Fact("Luogo", "Padova"), 200, 120)
    big = fit_text(Fact("Luogo", "Padova"), 800, 480)
    assert big.value_px > small.value_px * 3
    long = fit_text(Fact("", "Festa al garage di Matteo con gli amici"), 300, 400)
    assert 1 < len(long.lines) <= 4 and " ".join(long.lines) == "Festa al garage di Matteo con gli amici"


def test_colored_blocks_carry_the_facts_never_empty(ctx, photos):
    opts = poster.Options(paper="a4", colors=8, occasion="Festa")
    lay = poster.plan(ctx, photos, opts)
    blocks = [c for c in lay.cells if c.piece.color]
    assert blocks and all(c.fact and c.fit and c.fill for c in blocks)
    assert len(blocks) == len(poster.facts_for(ctx, [c.piece.photo_id for c in lay.cells if c.piece.photo_id], opts))
    biggest = max(blocks, key=lambda c: c.w * c.h)
    assert biggest.fact.value == "Festa" and biggest.fill == poster.TEXT_BLOCKS[0][0]
    with pytest.raises(poster.PosterError):
        poster.Options(place="x" * 81)


def test_layout_api_sends_the_text_for_the_preview(client, photos):
    c, _ = client
    body = c.post("/api/posters/layout", json={"uids": photos, "paper": "a4", "colors": 3, "occasion": "Festa", "place": "Padova"}).json()
    texts = [cell["text"] for cell in body["cells"] if cell["text"]]
    assert len(texts) == 3 and {t["fg"] for t in texts} <= {"#fbf8f1", "#141414"}
    assert any(t["lines"] == ["Festa"] for t in texts) and all(0 < t["value"] < 1 for t in texts)
    assert {"label": "Luogo", "value": "Padova"} in body["facts"]


def test_poster_folder_is_never_indexed_and_plans_are_reused(ctx, photos, tmp_path):
    out = tmp_path / "Immagini" / "Mosaic" / "p.jpg"
    opts = poster.Options(paper="a4", dpi=72)
    assert poster.plan(ctx, photos, opts) is poster.plan(ctx, photos, opts)  # l'anteprima e il file usano lo stesso piano
    poster.render(ctx, photos, opts, out)
    assert (out.parent / ".mosaicstorage").exists()


# --- salvati nell'album di riferimento (D-27) ---------------------------------------------------------

def test_folder_albums_keep_posters_in_a_mosaic_subfolder(ctx, library, photos):
    from mosaic.organize import moments

    moments.generate(ctx)
    folder = ctx.db.one("SELECT * FROM albums WHERE type='folder' AND filter LIKE 'root:%'")
    assert poster.album_dir(ctx, folder) == library / "Mosaic"
    manual = {"filter": "", "title": "Gita al lago!"}
    assert poster.album_dir(ctx, manual) == poster.posters_dir() / "gita-al-lago"


def test_create_saves_into_the_album_and_lists_it(client, photos, ctx, library):
    from mosaic.jobs.handlers import h_poster
    from mosaic.jobs.queue import Job
    from mosaic.organize import moments

    c, m = client
    moments.generate(ctx)
    album = ctx.db.one("SELECT uid FROM albums WHERE type='folder' AND filter LIKE 'root:%'")["uid"]
    assert c.post("/api/posters", json={"uids": photos[:2], "dpi": 72}).status_code == 400  # serve l'album
    res = c.post("/api/posters", json={"uids": photos[:2], "paper": "a4", "dpi": 72, "album": album}).json()
    assert res["folder"] == str(library / "Mosaic")
    row = m.runner.queue.get(res["job"])

    class Ctl:
        def progress(self, *a, **k):
            pass

    h_poster(ctx, Job(row["id"], "poster", json.loads(row["payload"])), Ctl())
    assert (library / "Mosaic" / res["name"]).is_file() and (library / "Mosaic" / ".mosaicstorage").exists()
    listed = c.get(f"/api/albums/{album}/posters").json()
    assert listed["posters"][0]["name"] == res["name"]
    assert c.get(f"/api/albums/{album}/posters/{res['name']}").status_code == 200
    assert c.get(f"/api/albums/{album}/posters/manca.jpg").status_code == 404
    from mosaic.index.indexer import index_roots
    assert index_roots(ctx).new_photos == 0  # il poster non diventa una foto della libreria
