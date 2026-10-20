from pathlib import Path

import yaml
from conftest import make_jpeg

from mosaic.index.indexer import index_roots
from mosaic.meta import sources
from mosaic.organize import albums, backup, people, tags
from mosaic.pipeline import process_photo


def make_photo(ctx, library: Path, name: str = "a.jpg", color=(200, 20, 20)) -> int:
    make_jpeg(library / name, color=color, size=(200, 150))
    (pid,) = index_roots(ctx).changed_photo_ids
    process_photo(ctx, pid)
    return pid


def sha1_of(ctx, pid: int) -> str:
    return ctx.db.scalar("SELECT sha1 FROM files WHERE photo_id=?", (pid,))


def test_nothing_manual_means_no_backup_file(ctx, library):
    pid = make_photo(ctx, library)
    assert backup.snapshot(ctx.db.conn(), pid) == {}
    assert backup.write_backup(ctx, pid) is False
    assert not backup.backup_path(ctx, sha1_of(ctx, pid)).exists()


def test_manual_data_is_written_to_yaml_and_automatic_data_is_not(ctx, library):
    pid = make_photo(ctx, library)
    conn = ctx.db.conn()
    sources.set_text(conn, pid, "title", "Ducati sul palco", sources.MANUAL)
    sources.set_text(conn, pid, "caption", "Dall'LLM", sources.LLM)          # automatico: non va nel backup
    tags.add_keywords(conn, pid, ["superbike"], sources.MANUAL)
    tags.add_keywords(conn, pid, ["automatica"], sources.LLM)
    tags.set_photo_label(conn, pid, tags.get_or_create_label(conn, "Moto", "Veicoli"), 0, sources.MANUAL)
    tags.set_photo_label(conn, pid, tags.get_or_create_label(conn, "Cielo blu"), 10, sources.ONNX)
    conn.execute("UPDATE photos SET favorite=1 WHERE id=?", (pid,))
    assert backup.write_backup(ctx, pid) is True
    data = yaml.safe_load(backup.backup_path(ctx, sha1_of(ctx, pid)).read_text(encoding="utf-8"))
    assert data["title"] == "Ducati sul palco" and data["favorite"] is True and data["version"] == backup.VERSION
    assert "caption" not in data and data["keywords"] == ["superbike"] and data["labels"] == [{"name": "Moto", "category": "Veicoli"}]


def test_data_survives_a_lost_database(ctx, library, tmp_path):
    from dataclasses import replace

    from mosaic.context import Context

    pid = make_photo(ctx, library)
    conn = ctx.db.conn()
    sources.set_text(conn, pid, "title", "Titolo salvato", sources.MANUAL)
    sources.set_text(conn, pid, "caption", "Una didascalia", sources.MANUAL)
    tags.add_keywords(conn, pid, ["ricordo"], sources.MANUAL)
    conn.execute("UPDATE photos SET favorite=1, private=1 WHERE id=?", (pid,))
    album = albums.create_album(conn, "Vacanze")
    albums.add_photos(conn, album["id"], [pid])
    conn.execute("INSERT INTO markers(uid, file_id, photo_id, name, x, y, w, h) VALUES ('m1', (SELECT id FROM files LIMIT 1), ?, 'Anna', .1, .2, .3, .3)", (pid,))
    assert backup.backup_changed(ctx) == 1

    # nuovo database nello stesso data_dir? No: database perso, ma cartella di backup conservata
    fresh_cfg = replace(ctx.cfg, data_dir=str(tmp_path / "data2"))
    fresh = Context.open(fresh_cfg)
    try:
        (tmp_path / "data2" / "backup").mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copytree(ctx.backup_dir, fresh.backup_dir, dirs_exist_ok=True)
        (new_id,) = index_roots(fresh).changed_photo_ids
        assert process_photo(fresh, new_id)
        row = fresh.db.one("SELECT * FROM photos WHERE id=?", (new_id,))
        assert (row["title"], row["title_src"], row["caption"]) == ("Titolo salvato", sources.MANUAL, "Una didascalia")
        assert row["favorite"] == 1 and row["private"] == 1
        assert tags.photo_keywords(fresh.db.conn(), new_id) == ["ricordo"]
        assert fresh.db.scalar("SELECT a.title FROM albums a JOIN photos_albums pa ON pa.album_id=a.id") == "Vacanze"
        assert fresh.db.scalar("SELECT name FROM subjects") == "Anna"
        assert fresh.db.scalar("SELECT COUNT(*) FROM photos_fts WHERE photos_fts MATCH 'salvato'") == 1
    finally:
        fresh.close()


def test_restore_never_overwrites_higher_priority_and_ignores_broken_files(ctx, library):
    pid = make_photo(ctx, library)
    sha1 = sha1_of(ctx, pid)
    path = backup.backup_path(ctx, sha1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": backup.VERSION, "title": "Dal backup"}), encoding="utf-8")
    conn = ctx.db.conn()
    sources.set_text(conn, pid, "title", "Admin", sources.ADMIN)
    assert backup.restore(ctx, pid) is True
    assert ctx.db.scalar("SELECT title FROM photos WHERE id=?", (pid,)) == "Admin"
    for broken in ("{non: valido", "- lista\n- non mappa", "version: 99\ntitle: x"):
        path.write_text(broken, encoding="utf-8")
        assert backup.restore(ctx, pid) is False
    assert backup.restore(ctx, 99999) is False


def test_backup_is_removed_when_manual_data_disappears(ctx, library):
    pid = make_photo(ctx, library)
    conn = ctx.db.conn()
    sources.set_text(conn, pid, "title", "Temporaneo", sources.MANUAL)
    backup.write_backup(ctx, pid)
    path = backup.backup_path(ctx, sha1_of(ctx, pid))
    assert path.exists()
    conn.execute("UPDATE photos SET title='', title_src=0 WHERE id=?", (pid,))
    assert backup.write_backup(ctx, pid) is False and not path.exists()


def test_backup_changed_only_visits_modified_photos(ctx, library):
    a = make_photo(ctx, library, "a.jpg")
    make_jpeg(library / "b.jpg", color=(1, 90, 200), size=(200, 150))
    (b,) = index_roots(ctx).changed_photo_ids
    process_photo(ctx, b)
    sources.set_text(ctx.db.conn(), a, "title", "Uno", sources.MANUAL)
    assert backup.backup_changed(ctx) == 1
    assert backup.backup_changed(ctx) in (0, 1)  # lo stesso secondo: può ripassare, ma non scrive file vuoti
    people.ensure_subject(ctx.db.conn(), "Qualcuno")
