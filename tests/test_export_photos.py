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


def test_convert_resize_and_number(ctx, photos, tmp_path):
    dest = tmp_path / "conv"
    opts = export_photos.Options(fmt="webp", quality=70, max_side=100, numbered=True)
    result = export_photos.export(ctx, photos, dest, opts)
    files = sorted(dest.iterdir())
    assert result.copied == 3 and result.converted == 3 and not result.errors
    assert [f.name[:5] for f in files] == ["0001_", "0002_", "0003_"] and all(f.suffix == ".webp" for f in files)
    for f in files:
        with Image.open(f) as img:
            assert img.format == "WEBP" and max(img.size) == 100


def test_max_side_never_upscales(ctx, photos, tmp_path):
    dest = tmp_path / "small"
    export_photos.export(ctx, photos, dest, export_photos.Options(fmt="png", max_side=5000))
    with Image.open(next(dest.glob("c.png"))) as img:
        assert img.format == "PNG" and img.size == (300, 300)


def test_failed_write_leaves_no_tmp_file(ctx, photos, tmp_path, monkeypatch):
    dest = tmp_path / "rotto"
    monkeypatch.setattr(export_photos.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disco pieno")))
    result = export_photos.export(ctx, photos, dest, export_photos.Options(fmt="jpeg"))
    assert result.errors and not list(dest.glob("*.tmp"))


def test_zip_download_via_api(client, photos, ctx):
    import zipfile

    from mosaic.jobs.handlers import h_export
    from mosaic.jobs.queue import Job

    c, m = client
    res = c.post("/api/exports", json={"uids": photos, "zip": True, "zip_name": "Gita: 2026", "fmt": "jpeg", "numbered": True})
    assert res.status_code == 202 and res.json()["folder"] == ""
    job_id = res.json()["job"]
    assert c.get(f"/api/exports/{job_id}/download").status_code == 404  # non ancora pronto
    row = m.runner.queue.get(job_id)

    class Ctl:
        cancel = threading.Event()

        def progress(self, *_args):
            pass

    h_export(ctx, Job(job_id, "export", json.loads(row["payload"])), Ctl())
    m.runner.queue.finish(job_id, "done", "")
    got = c.get(f"/api/exports/{job_id}/download")
    assert got.status_code == 200 and "Gita%202026.zip" in got.headers["content-disposition"]
    zpath = ctx.data_dir / "x.zip"
    zpath.write_bytes(got.content)
    with zipfile.ZipFile(zpath) as zf:
        assert len(zf.namelist()) == 3 and zf.namelist()[0].startswith("0001_")
    assert not list((ctx.data_dir / "cache" / "exports").glob("staging-*"))  # la cartella di lavoro non resta
    assert c.post("/api/exports", json={"uids": photos}).status_code == 422  # senza cartella né zip


def test_crop_and_enhance_still_write_jpegs_with_the_new_writer(ctx, photos, tmp_path):
    from mosaic.organize import smart_crop

    dest = tmp_path / "quadrate"
    result = export_photos.export(ctx, photos, dest, export_photos.Options(crop=smart_crop.Format(1, 1), enhance=enhance.Options()))
    assert result.cropped == 3 and result.enhanced == 3 and not result.errors
    for path in dest.iterdir():
        with Image.open(path) as img:
            assert img.format == "JPEG" and img.width == img.height
    assert not list(dest.glob("*.tmp"))


def test_export_fails_loudly_when_the_originals_are_not_where_the_catalog_says(ctx, photos, tmp_path):
    root = Path(ctx.cfg.library_roots[0])
    moved = tmp_path / "spostata"
    import shutil
    shutil.move(str(root), str(moved))
    with pytest.raises(export_photos.ExportError, match="non si trovano più"):
        export_photos.export(ctx, photos, tmp_path / "out", export_photos.Options())
    assert ctx.db.scalar("SELECT COUNT(*) FROM files WHERE missing=1") >= 3  # ora risultano mancanti nel catalogo


def test_partial_export_still_succeeds_and_reports_the_missing_ones(ctx, photos, tmp_path):
    original = abs_path(primary_file(ctx.db, ctx.db.one("SELECT id FROM photos WHERE uid=?", (photos[0],))["id"]))
    original.unlink()
    result = export_photos.export(ctx, photos, tmp_path / "parziale", export_photos.Options())
    assert result.copied == 2 and result.not_found == 1
