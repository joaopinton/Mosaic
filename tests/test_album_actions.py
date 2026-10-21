"""Azioni dal menu «⋯» di album, selezioni e cartelle (D-32): nome dato a mano, nascondere, aprire la cartella."""

import shutil
from pathlib import Path

import pytest
import yaml
from conftest import make_jpeg
from fastapi.testclient import TestClient

from mosaic.api import routes_collections
from mosaic.api.app import create_app
from mosaic.index.indexer import index_roots
from mosaic.organize import backup, moments
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


@pytest.fixture()
def folders(ctx, m) -> dict[str, dict]:
    lib = Path(ctx.cfg.library_roots[0])
    make_jpeg(lib / "Viaggi" / "Roma" / "a.jpg", color=(200, 20, 20))
    make_jpeg(lib / "Viaggi" / "Roma" / "b.jpg", color=(20, 200, 20))
    make_jpeg(lib / "Feste" / "c.jpg", color=(20, 20, 200))
    index_roots(ctx)
    moments.generate(ctx)
    return {a["slug"]: a for a in TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False))
            .get("/api/albums", params={"type": "folder"}).json()}


def by_title(client, title: str, **params) -> dict | None:
    return next((a for a in client.get("/api/albums", params={"type": "folder", **params}).json() if a["title"] == title), None)


def test_folder_albums_know_their_folder_on_disk(ctx, folders):
    lib = Path(ctx.cfg.library_roots[0])
    roma = next(a for a in folders.values() if a["title"] == "Roma")
    viaggi = next(a for a in folders.values() if a["title"] == "Viaggi")
    root = next(a for a in folders.values() if a["slug"].startswith("radice-"))
    assert Path(roma["path"]) == lib / "Viaggi" / "Roma" and roma["root"] is None
    assert Path(viaggi["path"]) == lib / "Viaggi"
    # solo la cartella data alla libreria si può scollegare (D-20)
    assert Path(root["path"]) == lib and root["root"] == str(lib)


def test_renamed_folder_keeps_its_name_when_albums_regenerate(client, ctx, folders):
    roma = by_title(client, "Roma")
    r = client.patch(f"/api/albums/{roma['uid']}", json={"title": "Roma 2024"})
    assert r.status_code == 200 and r.json()["title"] == "Roma 2024" and r.json()["renamed"] is True
    moments.generate(ctx)
    again = client.get(f"/api/albums/{roma['uid']}").json()
    assert again["title"] == "Roma 2024" and again["filter"] == roma["filter"]


def test_renaming_a_manual_album_is_not_marked_as_renamed(client, folders):
    album = client.post("/api/albums", json={"title": "Scelte"}).json()
    assert client.patch(f"/api/albums/{album['uid']}", json={"title": "Le migliori"}).json()["renamed"] is False


def test_hidden_folder_leaves_the_list_and_stays_hidden(client, ctx, folders):
    feste = by_title(client, "Feste")
    assert client.patch(f"/api/albums/{feste['uid']}", json={"private": True}).status_code == 200
    assert by_title(client, "Feste") is None
    moments.generate(ctx)   # la rigenerazione non la rimette in vista
    hidden = by_title(client, "Feste", hidden=True)
    assert hidden is not None and hidden["private"] is True
    # le foto restano nella libreria
    assert client.get("/api/photos", params={"q": f"album:{feste['uid']}"}).json()["total"] == 1
    assert client.patch(f"/api/albums/{feste['uid']}", json={"private": False}).status_code == 200
    assert by_title(client, "Feste") is not None


def test_automatic_albums_are_hidden_not_deleted(client, folders):
    assert client.delete(f"/api/albums/{by_title(client, 'Feste')['uid']}").status_code == 400


def test_reveal_opens_only_the_folder_from_the_catalog(client, ctx, folders, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr(routes_collections.os, "name", "nt")
    monkeypatch.setattr(routes_collections.os, "startfile", opened.append, raising=False)
    roma = by_title(client, "Roma")
    assert client.post(f"/api/albums/{roma['uid']}/reveal").status_code == 204
    assert opened == [Path(roma["path"])]
    album = client.post("/api/albums", json={"title": "Scelte"}).json()
    assert client.post(f"/api/albums/{album['uid']}/reveal").status_code == 400
    assert client.post("/api/albums/inesistente/reveal").status_code == 404
    shutil.rmtree(Path(roma["path"]))
    r = client.post(f"/api/albums/{roma['uid']}/reveal")
    assert r.status_code == 404 and "non c'è più" in r.json()["detail"]
    assert len(opened) == 1


def test_restore_title_gives_back_the_name_from_mosaic(client, ctx, folders):
    roma = by_title(client, "Roma")
    client.patch(f"/api/albums/{roma['uid']}", json={"title": "Roma 2024"})
    r = client.post(f"/api/albums/{roma['uid']}/restore-title")
    assert r.status_code == 200 and r.json()["title"] == "Roma" and r.json()["renamed"] is False
    moments.generate(ctx)
    assert client.get(f"/api/albums/{roma['uid']}").json()["title"] == "Roma"
    album = client.post("/api/albums", json={"title": "Scelte"}).json()
    assert client.post(f"/api/albums/{album['uid']}/restore-title").status_code == 400


def test_manual_names_and_hidden_folders_survive_a_lost_catalog(client, ctx, folders):
    client.patch(f"/api/albums/{by_title(client, 'Roma')['uid']}", json={"title": "Roma 2024"})
    client.patch(f"/api/albums/{by_title(client, 'Feste')['uid']}", json={"private": True})
    assert backup.write_album_names(ctx) is True
    assert backup.write_album_names(ctx) is False   # niente di nuovo: il file non si riscrive
    saved = yaml.safe_load(backup.album_names_path(ctx).read_text(encoding="utf-8"))["albums"]
    assert {(a["slug"], a.get("title"), a.get("hidden", False)) for a in saved} == {
        ("viaggi-roma", "Roma 2024", False), ("feste", None, True)}
    ctx.db.conn().execute("DELETE FROM albums WHERE type='folder'")   # catalogo perso o cartelle staccate
    moments.generate(ctx)
    roma = by_title(client, "Roma 2024")
    assert roma is not None and roma["renamed"] is True
    assert by_title(client, "Feste") is None and by_title(client, "Feste", hidden=True)["private"] is True


def test_saved_name_is_kept_while_the_folder_is_away_and_ignored_for_another_folder(client, ctx, folders):
    client.patch(f"/api/albums/{by_title(client, 'Roma')['uid']}", json={"title": "Roma 2024"})
    backup.write_album_names(ctx)
    ctx.db.conn().execute("DELETE FROM albums WHERE slug='viaggi-roma'")
    backup.write_album_names(ctx)   # l'album non c'è: la voce resta per quando torna
    assert "Roma 2024" in backup.album_names_path(ctx).read_text(encoding="utf-8")
    path = backup.album_names_path(ctx)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["albums"][0]["filter"] = 'folder:"Altro"'   # stessa chiave, un'altra cartella
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    moments.generate(ctx)
    assert by_title(client, "Roma") is not None and by_title(client, "Roma 2024") is None
