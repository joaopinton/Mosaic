"""Cartelle con sottocartelle e zip annidati (D-31): ogni sottocartella e ogni zip diventano una cartella in «Cartelle»."""

import json
import zipfile
from pathlib import Path

import pytest
from conftest import make_jpeg
from fastapi.testclient import TestClient

from mosaic.api.app import create_app
from mosaic.organize import dropin, moments
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


def jpeg(tmp_path: Path, name: str, color) -> bytes:
    return make_jpeg(tmp_path / "gen" / name, color=color).read_bytes()


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def viaggi(tmp_path: Path) -> Path:
    """Viaggi/{Roma,Parigi/Louvre} con foto, un zip «Mare.zip» con dentro una sottocartella, uno «Neve.zip» annidato."""
    src = tmp_path / "esterna" / "Viaggi"
    make_jpeg(src / "Roma" / "colosseo.jpg", color=(200, 10, 10))
    make_jpeg(src / "Roma" / "fontana.jpg", color=(10, 200, 10))
    make_jpeg(src / "Parigi" / "Louvre" / "piramide.jpg", color=(10, 10, 200))
    (src / "Parigi" / "Louvre" / "piramide.xmp").write_text("<x/>")
    make_zip(src / "Mare.zip", {"spiaggia.jpg": jpeg(tmp_path, "s.jpg", (90, 90, 10)),
                                "Tramonto/sole.jpg": jpeg(tmp_path, "t.jpg", (10, 90, 90))})
    make_zip(src / "Parigi" / "Neve.zip", {"Neve/pista.jpg": jpeg(tmp_path, "n.jpg", (90, 10, 90))})
    return src


def files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def folder_albums(ctx) -> dict[str, dict]:
    return {a["title"]: dict(a) for a in ctx.db.all("SELECT * FROM albums WHERE type='folder' AND deleted_at IS NULL")}


# --- anteprima ---------------------------------------------------------------------------------------

def test_inspect_counts_subfolders_and_nested_zips(ctx, tmp_path):
    src = viaggi(tmp_path)
    item = dropin.inspect(ctx, [str(src)])[0]
    assert (item.kind, item.media, item.zips, item.dest_name) == ("folder", 6, 2, "Viaggi")
    # Viaggi, Roma, Parigi, Louvre, Mare, Mare/Tramonto, Neve (lo zip «Neve.zip» contiene già «Neve/»: niente Neve/Neve)
    assert item.folders == 7
    z = dropin.inspect(ctx, [str(src / "Mare.zip")])[0]
    assert (z.kind, z.media, z.folders, z.dest_name) == ("zip", 2, 2, "Mare")


# --- copia nella libreria ----------------------------------------------------------------------------

def test_copied_folder_keeps_its_tree_and_nested_zips_get_their_own_folder(ctx, tmp_path, library):
    src = viaggi(tmp_path)
    stats = dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    assert stats.imported == 6 and not stats.errors
    assert files_under(library) == [
        "Viaggi/Mare/Tramonto/sole.jpg", "Viaggi/Mare/spiaggia.jpg",
        "Viaggi/Parigi/Louvre/piramide.jpg", "Viaggi/Parigi/Louvre/piramide.xmp",
        "Viaggi/Parigi/Neve/pista.jpg",
        "Viaggi/Roma/colosseo.jpg", "Viaggi/Roma/fontana.jpg",
    ]
    assert (src / "Mare.zip").exists() and not (src / "Mare").exists()   # l'originale non si tocca
    assert not any((ctx.data_dir / "cache" / "imports").iterdir())
    moments.generate(ctx)
    found = folder_albums(ctx)
    for title in ("Viaggi", "Roma", "Parigi", "Louvre", "Mare", "Tramonto", "Neve"):
        assert title in found, title
    assert found["Viaggi"]["photo_count"] == 6 and found["Roma"]["photo_count"] == 2
    assert found["Parigi"]["photo_count"] == 2 and found["Mare"]["photo_count"] == 2
    assert found["Tramonto"]["filter"] == 'folder:"Viaggi/Mare/Tramonto"'


def test_dropping_the_same_folder_again_copies_nothing_twice(ctx, tmp_path, library):
    src = viaggi(tmp_path)
    dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    again = dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    assert (again.imported, again.skipped) == (0, 6)
    assert len(files_under(library)) == 7


def test_same_photo_twice_in_one_drop_is_copied_once_and_names_never_clash(ctx, tmp_path, library):
    src = tmp_path / "esterna" / "Doppi"
    make_jpeg(src / "a" / "foto.jpg", color=(1, 2, 3))
    (src / "b").mkdir()
    (src / "b" / "foto.jpg").write_bytes((src / "a" / "foto.jpg").read_bytes())
    make_jpeg(library / "Doppi" / "a" / "foto.jpg", color=(250, 250, 250))   # stesso nome, altro contenuto
    stats = dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    assert (stats.imported, stats.skipped) == (1, 1)
    assert files_under(library / "Doppi") == ["a/foto (2).jpg", "a/foto.jpg"]


def test_dropped_zip_goes_into_a_folder_named_after_it(ctx, tmp_path, library):
    a = make_zip(tmp_path / "Gita al lago.zip", {"Gita al lago/x.jpg": jpeg(tmp_path, "x.jpg", (5, 50, 5)),
                                                 "Gita al lago/Sera/y.jpg": jpeg(tmp_path, "y.jpg", (50, 5, 5))})
    b = make_zip(tmp_path / "Compleanno.zip", {"z.jpg": jpeg(tmp_path, "z.jpg", (5, 5, 50))})
    stats = dropin.run_drop(ctx, {"zips": [str(a), str(b)]})
    assert stats.imported == 3 and not stats.errors
    assert files_under(library) == ["Compleanno/z.jpg", "Gita al lago/Sera/y.jpg", "Gita al lago/x.jpg"]
    moments.generate(ctx)
    assert {"Gita al lago", "Sera", "Compleanno"} <= set(folder_albums(ctx))


def test_nested_zip_keeps_the_d25_protections(ctx, tmp_path, library):
    src = tmp_path / "esterna" / "Misto"
    make_jpeg(src / "ok.jpg", color=(9, 9, 9))
    make_zip(src / "bomba.zip", {"big.jpg": b"\0" * 2_000_000})
    make_zip(src / "trucchi.zip", {"../fuori.jpg": jpeg(tmp_path, "f.jpg", (1, 1, 1)), "virus.exe": b"MZ",
                                   "dentro.jpg": jpeg(tmp_path, "d.jpg", (2, 2, 2))})
    stats = dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    assert stats.imported == 2
    assert any("bomba.zip" in e and "sospetto" in e for e in stats.errors)
    assert files_under(library) == ["Misto/ok.jpg", "Misto/trucchi/dentro.jpg"]
    assert not (tmp_path / "esterna" / "fuori.jpg").exists() and not (library / "fuori.jpg").exists()


def test_not_enough_space_for_a_copied_folder_is_an_error_not_a_half_copy(ctx, tmp_path, library, monkeypatch):
    src = viaggi(tmp_path)
    monkeypatch.setattr(dropin, "SPACE_MARGIN", 10 ** 18)
    stats = dropin.run_drop(ctx, {"copy_folders": [str(src)]})
    assert stats.imported == 0 and any("spazio insufficiente" in e for e in stats.errors)
    assert files_under(library) == []


def test_a_folder_containing_the_library_is_copied_without_the_library(ctx, tmp_path, library):
    make_jpeg(library / "gia.jpg", color=(3, 3, 3))
    make_jpeg(library.parent / "Altro" / "nuova.jpg", color=(4, 4, 4))
    stats = dropin.run_drop(ctx, {"copy_folders": [str(library.parent)]})
    assert stats.imported == 1 and not stats.errors
    assert f"{dropin.dest_name(library.parent)}/Altro/nuova.jpg" in files_under(library)


# --- cartella collegata --------------------------------------------------------------------------------

def test_unzip_here_extracts_next_to_the_zip_once(tmp_path):
    folder = tmp_path / "Foto"
    z = make_zip(folder / "Sub" / "Vacanze.zip", {"Vacanze/a.jpg": jpeg(tmp_path, "a.jpg", (7, 7, 7)),
                                                  "Vacanze/Giorno 2/b.jpg": jpeg(tmp_path, "b.jpg", (8, 8, 8))})
    assert dropin.unzip_here(z) == folder / "Sub" / "Vacanze"
    assert files_under(folder) == ["Sub/Vacanze.zip", "Sub/Vacanze/Giorno 2/b.jpg", "Sub/Vacanze/a.jpg"]
    assert dropin.unzip_here(z) is None                       # la cartella c'è già: non si tocca niente
    assert not [p for p in (folder / "Sub").iterdir() if p.name.startswith(".")]   # nessuna cartella temporanea
    empty = make_zip(folder / "vuoto.zip", {"leggimi.txt": b"x"})
    assert dropin.unzip_here(empty) is None and not (folder / "vuoto").exists()


def test_linked_folder_queues_the_unzip_before_indexing_and_subfolders_become_folders(client, m, tmp_path):
    src = viaggi(tmp_path)
    res = client.post("/api/drop/add", json={"paths": [str(src)], "folders": "link"})
    assert res.status_code == 202
    first, second = (m.runner.queue.get(j) for j in res.json()["jobs"])
    assert first["type"] == "drop" and second["type"] == "index"
    payload = json.loads(first["payload"])
    assert sorted(Path(p).name for p in payload["unzip_here"]) == ["Mare.zip", "Neve.zip"]
    assert payload["copy_folders"] == [] and payload["files"] == []
    stats = dropin.run_drop(m.ctx, payload)
    assert not stats.errors
    assert (src / "Mare" / "Tramonto" / "sole.jpg").exists() and (src / "Parigi" / "Neve" / "pista.jpg").exists()
    from mosaic.index.indexer import index_roots

    index_roots(m.ctx, [str(src.resolve())])
    moments.generate(m.ctx)
    found = folder_albums(m.ctx)
    assert {"Viaggi", "Roma", "Parigi", "Louvre", "Mare", "Tramonto", "Neve"} <= set(found)
    assert found["Parigi"]["photo_count"] == 2   # Parigi/Louvre e Parigi/Neve


def test_inspect_preview_reports_the_plan_fields(client, tmp_path):
    src = viaggi(tmp_path)
    item = client.post("/api/drop/inspect", json={"paths": [str(src)]}).json()["items"][0]
    assert (item["folders"], item["zips"], item["dest_name"]) == (7, 2, "Viaggi")


# --- browser -------------------------------------------------------------------------------------------

def test_browser_upload_keeps_folders_and_zips_apart(ctx, tmp_path, library):
    batch = dropin.upload_dir(ctx, "batch0003")
    make_jpeg(batch / "Festa" / "Torta" / "a.jpg", color=(60, 0, 0))
    make_zip(batch / "Festa" / "Amici.zip", {"b.jpg": jpeg(tmp_path, "b.jpg", (0, 60, 0))})
    make_zip(batch / "Gita.zip", {"c.jpg": jpeg(tmp_path, "c.jpg", (0, 0, 60))})
    make_jpeg(batch / "sciolta.jpg", color=(60, 60, 0))
    stats = dropin.run_drop(ctx, {"upload_dir": str(batch)})
    assert stats.imported == 4 and not stats.errors and not batch.exists()
    copied = files_under(library)
    assert {"Festa/Torta/a.jpg", "Festa/Amici/b.jpg", "Gita/c.jpg"} <= set(copied)
    assert len(copied) == 4 and not any(c.startswith(("Festa/sciolta", "Gita/sciolta")) for c in copied)


# --- album «Cartelle» ----------------------------------------------------------------------------------

def test_same_subfolder_in_two_roots_stays_two_folders(ctx):
    conn = ctx.db.conn()
    ids = []
    for root in ("D:\\Foto", "E:\\Archivio"):
        ids.append(conn.execute("INSERT INTO folders(root, path) VALUES (?, '2019/Vacanze')", (root,)).lastrowid)
    for n, fid in enumerate(ids):
        conn.execute("INSERT INTO photos(uid, taken_at_local, taken_at, folder_id, created_at, updated_at) "
                     "VALUES (?, '2019-08-01T10:00:00', '2019-08-01T10:00:00Z', ?, 'x', 'x')", (f"p{n}", fid))
    moments.generate(ctx)
    rows = ctx.db.all("SELECT slug, title, filter, photo_count FROM albums WHERE type='folder' AND slug NOT LIKE 'radice-%' "
                      "ORDER BY slug")
    by_slug = {r["slug"]: r for r in rows}
    assert set(by_slug) == {"2019", "2019-2", "2019-vacanze", "2019-vacanze-2"}
    assert by_slug["2019-vacanze"]["title"] == "Vacanze (Foto)" and by_slug["2019-vacanze-2"]["title"] == "Vacanze (Archivio)"
    assert all(r["photo_count"] == 1 for r in rows)
    assert by_slug["2019-vacanze"]["filter"] == 'root:"D:\\Foto" folder:"2019/Vacanze"'
