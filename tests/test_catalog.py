"""Foto e cartelle tolte dal catalogo (D-20): i file su disco non si perdono mai, i dati manuali tornano dal backup YAML."""

from dataclasses import replace
from pathlib import Path

import pytest
from conftest import make_jpeg

from mosaic.index.indexer import index_roots
from mosaic.meta import sources
from mosaic.organize import albums, backup, catalog, moments, trash
from mosaic.organize.importer import import_files
from mosaic.pipeline import process_photo
from mosaic.thumbs.render import thumb_path
from mosaic.thumbs.sizes import get as get_size


def load(ctx, lib: Path, *files: tuple[str, tuple[int, int, int]]) -> list[int]:
    for name, color in files:
        make_jpeg(lib / name, color=color, size=(640, 480))
    ids = index_roots(ctx, [str(lib)]).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    return ids


def set_title(ctx, pid: int, title: str) -> None:
    with ctx.db.tx() as conn:
        sources.set_text(conn, pid, "title", title, sources.MANUAL)


def count(ctx, sql: str, *args) -> int:
    return ctx.db.scalar(sql, args)


def detach(ctx, root: Path) -> None:
    """Come fa la rotta: la cartella esce dalla configurazione prima che parta il job di scollegamento."""
    ctx.cfg = replace(ctx.cfg, library_roots=tuple(r for r in ctx.cfg.library_roots if r != str(root)))


@pytest.fixture()
def lib2(ctx, library, tmp_path):
    other = tmp_path / "altra"
    other.mkdir()
    ctx.cfg = replace(ctx.cfg, library_roots=(str(library), str(other)))
    return other


# --- eliminare foto ---------------------------------------------------------------------------------

def test_remove_in_catalog_mode_keeps_the_file_and_drops_the_rest(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    set_title(ctx, pid, "Tramonto")
    thumb = thumb_path(ctx.thumbs_dir, sha1, get_size("tile_224"))
    assert thumb.exists()

    result = catalog.remove_photos(ctx, [pid], "catalog")

    assert (result.removed, result.failed) == (1, [])
    assert (library / "a.jpg").exists()
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 0 and count(ctx, "SELECT COUNT(*) FROM files") == 0
    assert count(ctx, "SELECT COUNT(*) FROM photos_fts") == 0
    assert not thumb.exists()
    assert count(ctx, "SELECT COUNT(*) FROM excluded_files WHERE sha1=?", sha1) == 1


def test_excluded_photo_is_not_reindexed_even_if_renamed_or_moved(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    catalog.remove_photos(ctx, [pid], "catalog")

    assert index_roots(ctx).new_photos == 0  # stesso percorso: saltata senza rileggerla
    (library / "a.jpg").rename(library / "rinominata.jpg")
    stats = index_roots(ctx)
    assert stats.new_photos == 0 and stats.excluded >= 1  # stesso contenuto: la riconosce dall'hash
    (library / "sotto").mkdir()
    (library / "rinominata.jpg").rename(library / "sotto" / "x.jpg")
    assert index_roots(ctx).new_photos == 0
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 0


def test_restoring_an_excluded_photo_brings_manual_data_back(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    set_title(ctx, pid, "Tramonto")
    catalog.remove_photos(ctx, [pid], "catalog")
    (listed,) = catalog.excluded_list(ctx)
    assert listed["name"] == "a.jpg" and listed["exists"] is True

    assert catalog.restore_excluded(ctx, [listed["sha1"]]) == 1
    (new_pid,) = index_roots(ctx).changed_photo_ids
    process_photo(ctx, new_pid)

    assert ctx.db.scalar("SELECT title FROM photos WHERE id=?", (new_pid,)) == "Tramonto"


def test_restore_all_excluded(ctx, library):
    ids = load(ctx, library, ("a.jpg", (200, 20, 20)), ("b.jpg", (20, 200, 20)))
    catalog.remove_photos(ctx, ids, "catalog")
    assert catalog.restore_excluded(ctx) == 2
    assert catalog.excluded_list(ctx) == []


def test_remove_in_trash_mode_sends_every_file_of_the_shot_to_the_trash(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    sent: list[list[Path]] = []

    def fake_trash(paths):
        sent.append(list(paths))
        for p in paths:
            p.unlink()

    result = catalog.remove_photos(ctx, [pid], "trash", trash_fn=fake_trash)

    assert result.removed == 1 and sent == [[library / "a.jpg"]]
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 0
    assert count(ctx, "SELECT COUNT(*) FROM excluded_files") == 0  # nel Cestino: se torna, si reindicizza


def test_trash_failure_keeps_the_photo_in_the_catalog(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))

    def no_trash(paths):
        raise trash.TrashUnavailable("questa unità non ha un Cestino")

    result = catalog.remove_photos(ctx, [pid], "trash", trash_fn=no_trash)

    assert result.removed == 0 and result.failed == [(pid, "questa unità non ha un Cestino")]
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 1 and (library / "a.jpg").exists()


def test_unknown_delete_mode_is_rejected(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    with pytest.raises(ValueError):
        catalog.remove_photos(ctx, [pid], "permanent")
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 1


def test_recycle_bin_refuses_drives_without_one_and_touches_nothing(tmp_path, monkeypatch):
    f = make_jpeg(tmp_path / "x.jpg")
    monkeypatch.setattr(trash, "has_recycle_bin", lambda path: False)
    with pytest.raises(trash.TrashUnavailable):
        trash.send_to_recycle_bin([f])
    assert f.exists()
    trash.send_to_recycle_bin([tmp_path / "non-esiste.jpg"])  # niente da fare, nessun errore


def test_import_skips_excluded_photos_and_keeps_the_source(ctx, library, tmp_path):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    catalog.remove_photos(ctx, [pid], "catalog")
    source = tmp_path / "sd"
    make_jpeg(source / "a.jpg", color=(200, 20, 20), size=(640, 480))
    # stesso contenuto della foto tolta: l'importazione non la copia e, anche con "sposta", non cancella la sorgente
    stats = import_files(ctx, source, move=True)
    assert stats.imported == 0 and stats.skipped == 1 and (source / "a.jpg").exists()


# --- scollegare una cartella ------------------------------------------------------------------------

def test_unlink_root_only_touches_that_folder_and_never_the_files(ctx, library, lib2):
    keep = load(ctx, library, ("a.jpg", (200, 20, 20)))
    gone = load(ctx, lib2, ("b.jpg", (20, 200, 20)), ("c.jpg", (20, 20, 200)))
    sha1s = [r[0] for r in ctx.db.conn().execute("SELECT sha1 FROM files WHERE photo_id IN (?, ?)", gone)]
    moments.generate(ctx)
    assert count(ctx, "SELECT COUNT(*) FROM albums WHERE filter LIKE ?", '%altra%') == 1

    detach(ctx, lib2)
    stats = catalog.unlink_root(ctx, str(lib2))

    assert (stats.photos, stats.files) == (2, 2)
    assert (lib2 / "b.jpg").exists() and (lib2 / "c.jpg").exists()
    assert [r["id"] for r in ctx.db.all("SELECT id FROM photos")] == keep
    assert count(ctx, "SELECT COUNT(*) FROM folders WHERE root LIKE ?", '%altra%') == 0
    assert count(ctx, "SELECT COUNT(*) FROM albums WHERE filter LIKE ?", '%altra%') == 0
    assert not any(thumb_path(ctx.thumbs_dir, s, get_size("tile_224")).exists() for s in sha1s)
    assert catalog.unlinked_roots(ctx) == [str(lib2)]


def test_relinked_folder_comes_back_with_manual_data_and_a_fresh_analysis(ctx, library, lib2):
    (pid,) = load(ctx, lib2, ("b.jpg", (20, 200, 20)))
    with ctx.db.tx() as conn:
        sources.set_text(conn, pid, "title", "Gita al lago", sources.MANUAL)
        conn.execute("UPDATE photos SET favorite=1, analyzed_at='x', faces_at='x', captioned_at='x' WHERE id=?", (pid,))
        album = albums.create_album(conn, "Vacanze")
        albums.add_photos(conn, album["id"], [pid])
    detach(ctx, lib2)
    catalog.unlink_root(ctx, str(lib2))

    assert count(ctx, "SELECT COUNT(*) FROM photos") == 0
    assert count(ctx, "SELECT COUNT(*) FROM albums WHERE title='Vacanze'") == 1  # album manuali e persone restano
    assert catalog.unlinked_roots(ctx) == [str(lib2)]

    ctx.cfg = replace(ctx.cfg, library_roots=(*ctx.cfg.library_roots, str(lib2)))  # ricollegata: si rifà tutto
    (new_pid,) = index_roots(ctx, [str(lib2)]).changed_photo_ids
    process_photo(ctx, new_pid)
    row = ctx.db.one("SELECT title, favorite, analyzed_at, faces_at, captioned_at FROM photos WHERE id=?", (new_pid,))
    assert (row["title"], row["favorite"]) == ("Gita al lago", 1)
    assert row["analyzed_at"] is None and row["faces_at"] is None and row["captioned_at"] is None
    assert count(ctx, "SELECT COUNT(*) FROM photos_albums pa JOIN albums a ON a.id=pa.album_id WHERE a.title='Vacanze'") == 1
    assert catalog.unlinked_roots(ctx) == []  # ricollegata: non è più «scollegata»


def test_unlink_writes_the_yaml_backup_first(ctx, library, lib2):
    (pid,) = load(ctx, lib2, ("b.jpg", (20, 200, 20)))
    sha1 = ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))
    set_title(ctx, pid, "Gita al lago")
    assert not backup.backup_path(ctx, sha1).exists()
    catalog.unlink_root(ctx, str(lib2))
    assert backup.backup_path(ctx, sha1).exists()


def test_unlink_keeps_photos_that_also_live_in_another_folder(ctx, library, lib2):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    ctx.db.conn().execute(
        "INSERT INTO files(photo_id, root, dir, name, original_name, sha1, media_type) VALUES (?,?,?,?,?,?,?)",
        (pid, str(lib2), "", "x.jpg", "x.jpg", "ff" * 20, "image"))
    catalog.unlink_root(ctx, str(lib2))
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 1 and count(ctx, "SELECT COUNT(*) FROM files") == 1


def test_unlink_unknown_root_is_a_noop(ctx, library):
    load(ctx, library, ("a.jpg", (200, 20, 20)))
    assert catalog.unlink_root(ctx, str(library / "mai-vista")).files == 0
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 1


def test_index_skips_a_root_unlinked_while_it_runs(ctx, library):
    make_jpeg(library / "a.jpg", size=(640, 480))
    ctx.cfg = replace(ctx.cfg, library_roots=())
    stats = index_roots(ctx, None)
    stats_all = index_roots(ctx, [str(library)])  # una richiesta esplicita per quella cartella invece funziona
    assert stats.scanned == 0 and stats_all.new_photos == 1


def test_recycle_bin_refuses_when_windows_would_delete_instead(tmp_path, monkeypatch):
    f = make_jpeg(tmp_path / "x.jpg")
    monkeypatch.setattr(trash, "has_recycle_bin", lambda path: True)
    monkeypatch.setattr(trash, "_volume_settings", lambda anchor: {"NukeOnDelete": 1})  # «elimina subito»
    with pytest.raises(trash.TrashUnavailable, match="elimina subito"):
        trash.send_to_recycle_bin([f])
    monkeypatch.setattr(trash, "_volume_settings", lambda anchor: {"MaxCapacity": 0, "NukeOnDelete": 0})
    f.write_bytes(b"x" * 2_000_000)
    monkeypatch.setattr(trash, "_volume_settings", lambda anchor: {"MaxCapacity": 1})  # Cestino da 1 MB, file da 2 MB
    with pytest.raises(trash.TrashUnavailable, match="più grande del Cestino"):
        trash.send_to_recycle_bin([f])
    assert f.exists()


def test_partial_trash_failure_marks_the_moved_files_as_missing(ctx, library):
    (pid,) = load(ctx, library, ("a.jpg", (200, 20, 20)))
    (library / "a.xmp").write_text("<x/>")
    with ctx.db.tx() as conn:
        conn.execute("INSERT INTO files(photo_id, root, dir, name, original_name, sha1, media_type) VALUES (?,?,?,?,?,?,?)",
                     (pid, str(library), "", "a.xmp", "a.xmp", "ab" * 20, "sidecar"))

    def half(paths):
        paths = list(paths)
        paths[0].unlink()  # il primo file arriva nel Cestino, poi Windows si ferma
        raise OSError("file bloccato da un altro programma")

    result = catalog.remove_photos(ctx, [pid], "trash", trash_fn=half)

    assert result.removed == 0 and "bloccato" in result.failed[0][1]
    assert count(ctx, "SELECT COUNT(*) FROM photos") == 1
    assert count(ctx, "SELECT COUNT(*) FROM files WHERE missing=1") == 1
