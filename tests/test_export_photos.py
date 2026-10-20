"""Esportazione delle foto in una cartella (D-30): copie, nomi, spazio, miglioramento, API e job."""

import json
import threading
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg
from fastapi.testclient import TestClient
from PIL import Image

from mosaic.api.app import create_app
from mosaic.index.indexer import index_roots
from mosaic.index.store import abs_path, primary_file
from mosaic.organize import enhance, export_photos
from mosaic.pipeline import process_photo
from mosaic.service import Mosaic


@pytest.fixture()
def photos(ctx, library):
    make_jpeg(library / "a" / "festa.jpg", color=(40, 30, 30), size=(320, 240))
    make_jpeg(library / "b" / "festa.jpg", color=(200, 180, 160), size=(240, 320))
    make_jpeg(library / "c.jpg", color=(30, 90, 160), size=(300, 300))
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    return [r["uid"] for r in ctx.db.all("SELECT uid FROM photos ORDER BY id")]


def test_copies_keep_names_and_never_overwrite(ctx, photos, tmp_path):
    dest = tmp_path / "Desktop" / "Festa"
    seen = []
    result = export_photos.export(ctx, photos, dest, export_photos.Options(), progress=lambda d, t, _: seen.append((d, t)))
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["c.jpg", "festa (2).jpg", "festa.jpg"]
    assert result.copied == 3 and not result.errors and seen[-1] == (3, 3)
    again = export_photos.export(ctx, photos[:1], dest, export_photos.Options())
    assert again.copied == 1 and len(list(dest.iterdir())) == 4  # «festa (3).jpg» o «c (2).jpg», mai sovrascritta
    original = abs_path(primary_file(ctx.db, ctx.db.one("SELECT id FROM photos WHERE uid=?", (photos[0],))["id"]))
    assert any(original.read_bytes() == p.read_bytes() for p in dest.iterdir())


def test_enhanced_copies_are_jpegs_and_originals_untouched(ctx, photos, tmp_path):
    dest = tmp_path / "out"
    before = {p: p.read_bytes() for p in Path(ctx.cfg.library_roots[0]).rglob("*.jpg")}
    result = export_photos.export(ctx, photos, dest, export_photos.Options(enhance=enhance.Options()))
    assert result.enhanced == 3
    for path in dest.glob("*.jpg"):
        with Image.open(path) as img:
            assert img.format == "JPEG"
    assert all(p.read_bytes() == data for p, data in before.items())
    darkest = min(float(np.asarray(Image.open(p).convert("L")).mean()) for p in dest.glob("*.jpg"))
    assert darkest > 40  # la foto scura (luminosità ~32) è stata schiarita


def test_refuses_bad_destinations_and_missing_space(ctx, photos, tmp_path, monkeypatch):
    with pytest.raises(export_photos.ExportError):
        export_photos.check_dest(ctx, "relativa\\cartella")
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(export_photos.ExportError):
        export_photos.check_dest(ctx, str(tmp_path / "file.txt"))
    with pytest.raises(export_photos.ExportError):
        export_photos.check_dest(ctx, str(ctx.cfg.data_path / "export"))
    monkeypatch.setattr(export_photos.shutil, "disk_usage", lambda _: type("U", (), {"free": 10})())
    with pytest.raises(export_photos.ExportError, match="spazio insufficiente"):
        export_photos.export(ctx, photos, tmp_path / "pieno", export_photos.Options())
    assert not (tmp_path / "pieno").exists()


def test_inside_the_library_the_folder_is_never_indexed(ctx, photos, library):
    from mosaic.index.discover import STORAGE_MARKER

    dest = library / "Esportate"
    export_photos.export(ctx, photos, dest, export_photos.Options())
    assert (dest / STORAGE_MARKER).exists()


def test_folder_names_and_default_dir():
    assert export_photos.folder_name('Festa: "Anna" 2025?') == "Festa Anna 2025"
    assert export_photos.folder_name("...") == "Foto Mosaic"
    assert export_photos.default_dir("Gita").name == "Gita"


@pytest.fixture()
def client(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    m = Mosaic(ctx)
    yield TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False)), m
    m.runner.stop()


def test_api_runs_the_export_as_a_job(client, photos, ctx, tmp_path):
    from mosaic.jobs.handlers import h_export
    from mosaic.jobs.queue import Job

    c, m = client
    dest = tmp_path / "Scrivania" / "Gita"
    res = c.post("/api/exports", json={"uids": photos, "dest": str(dest), "enhance": {"strength": "light"}})
    assert res.status_code == 202 and res.json()["folder"] == str(dest)
    row = m.runner.queue.get(res.json()["job"])
    assert row["type"] == "export"

    class Ctl:
        messages: list[str] = []
        cancel = threading.Event()

        def progress(self, value, message="", *_counts):
            self.messages.append(message)

    ctl = Ctl()
    h_export(ctx, Job(row["id"], "export", json.loads(row["payload"])), ctl)
    assert "3 foto esportate" in ctl.messages[-1] and "3 migliorate" in ctl.messages[-1]
    assert len(list(dest.glob("*.jpg"))) == 3
    assert c.post("/api/exports", json={"uids": photos, "dest": "relativa"}).status_code == 400
    assert c.post("/api/exports", json={"uids": ["nessuna"], "dest": str(dest)}).status_code == 404
    assert c.post("/api/exports", json={"uids": [], "dest": str(dest)}).status_code == 422
    assert c.post("/api/exports/999999/reveal").status_code == 404


def test_api_preview_default_dir_and_dialog(client, photos):
    c, _ = client
    res = c.post("/api/exports/preview", json={"uids": photos})
    assert res.status_code == 200
    shots = res.json()["photos"]
    assert len(shots) == 3 and all(s["before"].startswith("data:image/jpeg") and s["after"] != s["before"] for s in shots)
    assert c.get("/api/exports/default-dir", params={"title": "Festa/Notte"}).json()["folder"].endswith("Festa Notte")
    assert c.post("/api/dialog/folder", json={}).status_code == 501  # fuori dall'app desktop
