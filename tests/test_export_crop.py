"""Esportazione con «Ritaglia a formato» (D-34): formato esatto, volti, EXIF e profilo colore, video fuori, API e job."""

import json
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg, make_video
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from mosaic.api.app import create_app
from mosaic.index.indexer import index_roots
from mosaic.index.store import primary_file
from mosaic.organize import enhance, export_photos
from mosaic.organize.smart_crop import Format
from mosaic.pipeline import process_photo
from mosaic.service import Mosaic


def _with_metadata(path: Path, size: tuple[int, int]) -> Path:
    """Una foto con un soggetto rosso a sinistra, EXIF (marca e dimensioni) e profilo sRGB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, (60, 120, 60))
    img.paste((220, 30, 30), (20, 100, 120, 220))
    exif = Image.Exif()
    exif[0x010F] = "Mosaic Test"
    exif.get_ifd(0x8769)[0xA002], exif.get_ifd(0x8769)[0xA003] = size
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(path, "JPEG", quality=92, exif=exif.tobytes(), icc_profile=icc)
    return path


@pytest.fixture()
def shots(ctx, library):
    _with_metadata(library / "festa" / "anna.jpg", (600, 400))
    make_jpeg(library / "festa" / "torta.jpg", color=(200, 180, 160), size=(300, 450))
    make_video(library / "festa" / "ballo.mp4")
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    rows = ctx.db.all("SELECT p.id, p.uid, f.name FROM photos p JOIN files f ON f.photo_id=p.id ORDER BY p.id")
    return {r["name"]: (r["id"], r["uid"]) for r in rows}


def _face(ctx, photo_id: int, x: float, y: float, w: float, h: float) -> None:
    fid = primary_file(ctx.db, photo_id)["id"]
    ctx.db.conn().execute("INSERT INTO markers(uid, file_id, photo_id, x, y, w, h) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (f"m{photo_id}", fid, photo_id, x, y, w, h))


def test_cropped_copies_are_square_jpegs_with_exif_and_colour_profile(ctx, shots, tmp_path):
    before = {p: p.read_bytes() for p in Path(ctx.cfg.library_roots[0]).rglob("*.*")}
    dest = tmp_path / "Quadrate"
    result = export_photos.export(ctx, [uid for _, uid in shots.values()], dest, export_photos.Options(crop=Format(1, 1)))
    assert sorted(p.name for p in dest.iterdir()) == ["anna.jpg", "torta.jpg"]  # il video resta fuori
    assert result.cropped == 2 and result.copied == 2 and result.skipped == 1 and not result.errors
    for path in dest.iterdir():
        with Image.open(path) as img:
            assert img.format == "JPEG" and img.width == img.height
    with Image.open(dest / "anna.jpg") as img:
        exif = img.getexif()
        assert exif[0x010F] == "Mosaic Test" and img.info.get("icc_profile")
        assert (exif.get_ifd(0x8769)[0xA002], exif.get_ifd(0x8769)[0xA003]) == (400, 400)
    assert all(p.read_bytes() == data for p, data in before.items())  # originali intatti


def test_the_crop_keeps_the_face(ctx, shots, tmp_path):
    pid, uid = shots["anna.jpg"]
    _face(ctx, pid, 20 / 600, 100 / 400, 100 / 600, 120 / 400)  # il volto è il riquadro rosso a sinistra
    dest = tmp_path / "Volti"
    export_photos.export(ctx, [uid], dest, export_photos.Options(crop=Format(4, 5)))
    with Image.open(dest / "anna.jpg") as img:
        assert Fraction(*img.size) == Fraction(4, 5)
        red = np.asarray(img)[..., 0] > 180
    columns = np.flatnonzero(red.any(axis=0))
    assert abs(columns[0] - 20) <= 2 and red.sum() > 0.9 * 100 * 120  # volto intero, riquadro attaccato al bordo sinistro


def test_pixel_format_and_enhance_together(ctx, shots, tmp_path):
    uids = [shots["anna.jpg"][1], shots["torta.jpg"][1]]
    result = export_photos.export(ctx, uids, tmp_path / "Storie",
                                  export_photos.Options(crop=Format(1080, 1920, pixels=True), enhance=enhance.Options()))
    assert result.cropped == 2 and result.enhanced == 2
    for path in (tmp_path / "Storie").iterdir():
        with Image.open(path) as img:
            assert img.size == (1080, 1920)


def test_a_photo_that_cannot_be_cropped_is_not_copied_as_it_was(ctx, shots, tmp_path, monkeypatch):
    monkeypatch.setattr(export_photos, "ensure_source", lambda *_: None)
    result = export_photos.export(ctx, [shots["anna.jpg"][1]], tmp_path / "Rotte", export_photos.Options(crop=Format(1, 1)))
    assert result.copied == 0 and "non ritagliata" in result.errors[0]
    assert not list((tmp_path / "Rotte").glob("*.jpg"))


@pytest.fixture()
def client(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    m = Mosaic(ctx)
    yield TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False)), m
    m.runner.stop()


def test_api_crop_job_and_preview(client, shots, ctx, tmp_path):
    from mosaic.jobs.handlers import h_export
    from mosaic.jobs.queue import Job

    c, m = client
    uids = [uid for _, uid in shots.values()]
    dest = tmp_path / "Scrivania" / "A4"
    res = c.post("/api/exports", json={"uids": uids, "dest": str(dest), "crop": {"w": 210, "h": 297}})
    assert res.status_code == 202
    row = m.runner.queue.get(res.json()["job"])
    assert json.loads(row["payload"])["crop"] == {"w": 210, "h": 297, "pixels": False}

    class Ctl:
        messages: list[str] = []
        cancel = threading.Event()

        def progress(self, value, message="", *_counts):
            self.messages.append(message)

    ctl = Ctl()
    h_export(ctx, Job(row["id"], "export", json.loads(row["payload"])), ctl)
    assert "2 foto esportate" in ctl.messages[-1] and "2 ritagliate" in ctl.messages[-1] and "1 saltate" in ctl.messages[-1]
    for path in dest.glob("*.jpg"):
        with Image.open(path) as img:
            assert abs(img.width / img.height - 210 / 297) < 1 / img.width

    shots_ = c.post("/api/exports/crop-preview", json={"uids": uids, "crop": {"w": 16, "h": 9}}).json()["photos"]
    assert len(shots_) == 2 and all(s["image"].startswith("data:image/jpeg") for s in shots_)
    for s in shots_:
        x0, y0, x1, y1 = s["box"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert c.post("/api/exports", json={"uids": uids, "dest": str(dest), "crop": {"w": 0, "h": 1}}).status_code == 422
    assert c.post("/api/exports/crop-preview", json={"uids": uids, "crop": {"w": 20, "h": 1}}).status_code == 422
