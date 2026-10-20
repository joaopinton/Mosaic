"""Trascina e rilascia (D-25): anteprima, cartelle collegate o copiate, file sciolti, zip sicuri, caricamento dal browser."""

import json
import zipfile
from pathlib import Path

import pytest
from conftest import make_jpeg
from fastapi.testclient import TestClient

from mosaic.api.app import create_app
from mosaic.desktop import drop_script, dropped_paths
from mosaic.organize import dropin
from mosaic.service import Mosaic


@pytest.fixture()
def m(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    mosaic = Mosaic(ctx)
    yield mosaic
    mosaic.runner.stop()


@pytest.fixture()
def client(m):
    return TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False))


def outside(tmp_path: Path) -> Path:
    src = tmp_path / "esterna" / "Festa 2024"
    make_jpeg(src / "a.jpg", color=(200, 10, 10))
    make_jpeg(src / "sub" / "b.jpg", color=(10, 200, 10))
    (src / "note.txt").write_text("x")
    return src


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def jpeg_bytes(tmp_path: Path, name: str, color) -> bytes:
    return make_jpeg(tmp_path / "gen" / name, color=color).read_bytes()


# --- anteprima ---------------------------------------------------------------------------------------

def test_inspect_classifies_what_was_dropped(ctx, tmp_path, library):
    src = outside(tmp_path)
    z = make_zip(tmp_path / "foto.zip", {"x.jpg": jpeg_bytes(tmp_path, "x.jpg", (1, 2, 3)), "leggimi.txt": b"x"})
    inside = make_jpeg(library / "gia.jpg")
    items = {i.name: i for i in dropin.inspect(ctx, [str(src), str(src / "a.jpg"), str(z), str(src / "note.txt"),
                                                      str(inside), str(tmp_path / "manca.jpg"), "relativo.jpg"])}
    assert (items["Festa 2024"].kind, items["Festa 2024"].media) == ("folder", 2)
    assert items["a.jpg"].kind == "media" and items["foto.zip"].media == 1
    assert items["note.txt"].kind == "unsupported" and items["manca.jpg"].kind == "missing"
    assert items["relativo.jpg"].kind == "missing" and items["gia.jpg"].in_library


def test_a_folder_containing_the_library_is_flagged(ctx, library):
    item = dropin.inspect(ctx, [str(library.parent)])[0]
    assert item.contains_library and not item.in_library


# --- zip ---------------------------------------------------------------------------------------------

def test_zip_extraction_keeps_only_media_and_blocks_path_tricks(tmp_path):
    data = jpeg_bytes(tmp_path, "z.jpg", (5, 5, 5))
    z = make_zip(tmp_path / "a.zip", {"album/z.jpg": data, "../fuori.jpg": data, "C:/win.jpg": data, "/abs.jpg": data,
                                      "__MACOSX/._z.jpg": data, "virus.exe": b"MZ", "album/z.xmp": b"<x/>"})
    out = tmp_path / "out"
    extracted, skipped = dropin.extract_zip(z, out)
    assert extracted == 2 and skipped == 5
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["album/z.jpg", "album/z.xmp"]
    assert not (tmp_path / "fuori.jpg").exists()


def test_zip_bombs_and_broken_zips_are_refused(tmp_path, monkeypatch):
    bomb = make_zip(tmp_path / "bomb.zip", {"big.jpg": b"\0" * 2_000_000})
    with pytest.raises(dropin.DropError, match="compressione sospetto"):
        dropin.extract_zip(bomb, tmp_path / "o1")
    monkeypatch.setattr(dropin, "MAX_ZIP_BYTES", 10)
    small = make_zip(tmp_path / "s.zip", {"a.jpg": jpeg_bytes(tmp_path, "a.jpg", (9, 9, 9))})
    with pytest.raises(dropin.DropError, match="troppo grande"):
        dropin.extract_zip(small, tmp_path / "o2")
    (tmp_path / "rotto.zip").write_bytes(b"non uno zip")
    with pytest.raises(dropin.DropError, match="non è uno zip"):
        dropin.extract_zip(tmp_path / "rotto.zip", tmp_path / "o3")


# --- job ---------------------------------------------------------------------------------------------

def test_run_drop_copies_loose_files_with_their_xmp_and_zips(ctx, tmp_path, library):
    src = outside(tmp_path)
    (src / "a.xmp").write_text("<x/>")
    z = make_zip(tmp_path / "f.zip", {"c.jpg": jpeg_bytes(tmp_path, "c.jpg", (30, 60, 90))})
    seen = []
    stats = dropin.run_drop(ctx, {"files": [str(src / "a.jpg")], "zips": [str(z)]}, lambda d, t, label: seen.append((d, t)))
    assert stats.imported == 2 and not stats.errors and len(stats.photo_ids) == 2
    copied = sorted(p.suffix for p in library.rglob("*") if p.is_file())
    assert copied == [".jpg", ".jpg", ".xmp"]
    assert seen == [(1, 2), (2, 2)]
    assert (src / "a.jpg").exists()  # l'originale resta
    assert not any((ctx.data_dir / "cache" / "imports").iterdir())  # la cartella temporanea sparisce


def test_run_drop_needs_a_library_folder(ctx, tmp_path):
    from dataclasses import replace

    ctx.cfg = replace(ctx.cfg, library_roots=())
    with pytest.raises(dropin.DropError, match="collega prima"):
        dropin.run_drop(ctx, {"files": []})


def test_upload_targets_stay_inside_the_batch(ctx):
    good = dropin.upload_target(ctx, "abcd1234", "Vacanze/IMG_1.JPG")
    assert good.name == "IMG_1.JPG" and good.parent.name == "Vacanze"
    for bad_batch, bad_path in (("abcd1234", "../x.jpg"), ("abcd1234", "C:/x.jpg"), ("abcd1234", "doc.pdf"),
                                ("../evil", "x.jpg"), ("abcd1234", ".nascosto/x.jpg")):
        with pytest.raises(dropin.DropError):
            dropin.upload_target(ctx, bad_batch, bad_path)


# --- API ---------------------------------------------------------------------------------------------

def test_drop_api_links_a_folder_where_it_is(client, m, tmp_path):
    src = outside(tmp_path)
    preview = client.post("/api/drop/inspect", json={"paths": [str(src)]}).json()
    assert preview["items"][0]["kind"] == "folder" and preview["items"][0]["media"] == 2
    res = client.post("/api/drop/add", json={"paths": [str(src)], "folders": "link"})
    assert res.status_code == 202 and res.json()["linked"] == [str(src.resolve())]
    assert str(src.resolve()) in m.cfg.library_roots
    job = m.runner.queue.get(res.json()["jobs"][0])
    assert job["type"] == "index" and json.loads(job["payload"])["roots"] == [str(src.resolve())]


def test_drop_api_copies_files_and_rejects_nothing_useful(client, m, tmp_path):
    src = outside(tmp_path)
    res = client.post("/api/drop/add", json={"paths": [str(src / "a.jpg"), str(src)], "folders": "copy"})
    job = m.runner.queue.get(res.json()["jobs"][0])
    payload = json.loads(job["payload"])
    assert job["type"] == "drop" and payload["copy_folders"] == [str(src.resolve())]
    assert payload["files"] == [str((src / "a.jpg").resolve())]
    assert client.post("/api/drop/add", json={"paths": [str(src / "note.txt")]}).status_code == 400
    assert client.post("/api/drop/add", json={"paths": []}).status_code == 422


def test_browser_upload_then_finish_queues_a_drop_job(client, m, tmp_path):
    data = jpeg_bytes(tmp_path, "u.jpg", (1, 100, 1))
    ok = client.put("/api/drop/upload/batch0001", params={"path": "Gita/u.jpg"}, content=data)
    assert ok.status_code == 201 and ok.json()["size"] == len(data)
    assert client.put("/api/drop/upload/batch0001", params={"path": "../u.jpg"}, content=data).status_code == 400
    assert client.put("/api/drop/upload/BAD", params={"path": "u.jpg"}, content=data).status_code == 400
    finish = client.post("/api/drop/upload/batch0001/finish")
    assert finish.status_code == 202
    assert m.runner.queue.get(finish.json()["job"])["type"] == "drop"
    assert client.post("/api/drop/upload/vuoto0001/finish").status_code == 400
    assert client.delete("/api/drop/upload/batch0001").status_code == 204


def test_desktop_bridge_passes_real_paths_to_the_page():
    event = {"dataTransfer": {"files": [{"name": "a.jpg", "pywebviewFullPath": "D:\\Foto\\a.jpg"}, {"name": "b"}]}}
    assert dropped_paths(event) == ["D:\\Foto\\a.jpg"]
    assert dropped_paths({}) == []
    assert drop_script(["D:\\Foto \"x\"\\a.jpg"]) == 'window.mosaicDrop && window.mosaicDrop(["D:\\\\Foto \\"x\\"\\\\a.jpg"])'


def test_nested_or_repeated_folders_link_only_the_outer_one(client, m, tmp_path):
    src = outside(tmp_path)
    res = client.post("/api/drop/add", json={"paths": [str(src), str(src / "sub"), str(src)], "folders": "link"})
    assert res.json()["linked"] == [str(src.resolve())]
    assert m.cfg.library_roots.count(str(src.resolve())) == 1


def test_bad_content_length_is_a_client_error(client):
    res = client.put("/api/drop/upload/batch0001", params={"path": "a.jpg"}, content=b"x", headers={"content-length": "abc"})
    assert res.status_code in (400, 422)


def test_one_huge_ultra_compressed_entry_is_skipped_not_the_whole_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(dropin, "RATIO_MIN_BYTES", 1_000_000)
    data = jpeg_bytes(tmp_path, "ok.jpg", (10, 120, 30))
    noise = __import__("os").urandom(400_000)
    z = make_zip(tmp_path / "mix.zip", {"ok.jpg": data, "rumore.jpg": noise, "piatta.bmp": b"\0" * 2_000_000})
    extracted, skipped = dropin.extract_zip(z, tmp_path / "o")
    assert extracted == 2 and not (tmp_path / "o" / "piatta.bmp").exists()


def test_upload_folder_named_like_a_zip_does_not_break_the_job(ctx, tmp_path):
    folder = dropin.upload_dir(ctx, "batch0002")
    make_jpeg(folder / "Archivio.zip" / "a.jpg", color=(80, 80, 200))
    stats = dropin.run_drop(ctx, {"upload_dir": str(folder)})
    assert stats.imported == 1 and not folder.exists()
