import csv
import json
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import make_jpeg

from mosaic.index.hashing import hash_file
from mosaic.index.indexer import index_roots
from mosaic.meta import sources
from mosaic.organize import albums, cleanup, export, legacy, moments, people, tags
from mosaic.organize.importer import ImportFailed, import_files
from mosaic.organize.watcher import LibraryWatcher
from mosaic.search import query

ROOT = Path(__file__).resolve().parent.parent


def photo(ctx, uid, taken="2024-05-01T10:00:00", folder=None, **extra) -> int:
    conn = ctx.db.conn()
    fid = None
    if folder is not None:
        row = conn.execute("SELECT id FROM folders WHERE path=?", (folder,)).fetchone()
        fid = row["id"] if row else conn.execute("INSERT INTO folders(root, path) VALUES ('r', ?)", (folder,)).lastrowid
    cols = {"uid": uid, "taken_at_local": taken, "taken_at": taken + "Z", "folder_id": fid, "created_at": "x",
            "updated_at": "x", **extra}
    pid = conn.execute(f"INSERT INTO photos({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values())).lastrowid
    conn.execute("INSERT INTO files(photo_id, root, dir, name, original_name, sha1, media_type, is_primary) "
                 "VALUES (?, 'r', ?, ?, ?, ?, 'image', 1)", (pid, folder or "", f"{uid}.jpg", f"{uid}.jpg", uid.ljust(40, "0")))
    return pid


# --- album --------------------------------------------------------------------------------

def test_album_lifecycle(ctx):
    conn = ctx.db.conn()
    ids = [photo(ctx, f"p{i}") for i in range(3)]
    album = albums.create_album(conn, "Gita al lago")
    assert album["slug"] == "gita-al-lago" and album["uid"].startswith("a")
    assert albums.create_album(conn, "Gita al lago")["slug"] == "gita-al-lago-2"
    assert albums.add_photos(conn, album["id"], ids[:2]) == 2 and albums.add_photos(conn, album["id"], ids[:2]) == 0
    assert albums.add_photos(conn, album["id"], [9999]) == 0
    assert conn.execute("SELECT photo_count FROM albums WHERE id=?", (album["id"],)).fetchone()[0] == 2
    assert albums.remove_photos(conn, album["id"], [ids[0]]) == 1
    albums.update_album(conn, album["id"], title="Lago", favorite=1)
    assert albums.get_album(conn, "gita-al-lago")["title"] == "Lago" and albums.get_album(conn, album["uid"])["favorite"] == 1
    albums.delete_album(conn, album["id"])
    assert albums.get_album(conn, album["uid"]) is None


def test_album_validation(ctx):
    conn = ctx.db.conn()
    with pytest.raises(albums.AlbumError):
        albums.create_album(conn, "   ")
    with pytest.raises(albums.AlbumError):
        albums.create_album(conn, "x", album_type="boh")
    smart = albums.create_album(conn, "Rosse", album_type="moment", filter="color:rosso")
    with pytest.raises(albums.AlbumError):
        albums.add_photos(conn, smart["id"], [1])
    with pytest.raises(albums.AlbumError):
        albums.update_album(conn, smart["id"], title=" ")


def test_cover_prefers_explicit_choice_then_first_photo(ctx):
    conn = ctx.db.conn()
    a, b = photo(ctx, "a"), photo(ctx, "b")
    album = albums.create_album(conn, "X")
    assert albums.cover_hash(ctx, album) is None
    albums.add_photos(conn, album["id"], [a, b])
    assert albums.cover_hash(ctx, albums.get_album(conn, album["id"])).startswith("a")
    albums.update_album(conn, album["id"], cover_photo_id=b)
    assert albums.cover_hash(ctx, albums.get_album(conn, album["id"])).startswith("b")


# --- moments --------------------------------------------------------------------------------

def test_moments_create_update_and_remove(ctx):
    conn = ctx.db.conn()
    for i in range(6):
        photo(ctx, f"m{i}", taken=f"2024-05-0{i + 1}T10:00:00", folder="viaggi/maggio")
    photo(ctx, "lone", taken="2023-01-01T10:00:00", folder="altro")
    lid = tags.get_or_create_label(conn, "Moto", "Moto")
    for pid in range(1, 7):
        tags.set_photo_label(conn, pid, lid, 10, 12)
    conn.execute("INSERT INTO subjects(uid, name, slug, photo_count, created_at) VALUES ('s','Anna','anna',5,'x')")
    albums.create_album(conn, "Mio album manuale")
    stats = moments.generate(ctx)
    assert stats.threshold == moments.threshold_for(7) == 3
    found = {(a["type"], a["slug"]): a for a in conn.execute("SELECT * FROM albums").fetchall()}
    assert ("folder", "viaggi-maggio") in found and ("folder", "altro") in found
    assert ("month", "2024-05") in found and found[("month", "2024-05")]["title"] == "Maggio 2024"
    assert ("month", "2023-01") not in found                       # una sola foto: sotto soglia
    assert ("moment", "etichetta-moto") in found and ("moment", "persona-anna") in found
    assert found[("month", "2024-05")]["photo_count"] == 6 and found[("moment", "etichetta-moto")]["photo_count"] == 6
    assert found[("folder", "viaggi-maggio")]["photo_count"] == 6
    again = moments.generate(ctx)
    assert (again.created, again.updated, again.removed) == (0, 0, 0)
    conn.execute("UPDATE photos SET deleted_at='x' WHERE uid LIKE 'm%'")
    conn.execute("UPDATE subjects SET photo_count=0")
    after = moments.generate(ctx)
    assert after.removed >= 3
    remaining = {(a["type"], a["slug"]) for a in conn.execute("SELECT type, slug FROM albums")}
    assert ("album", "mio-album-manuale") in remaining and ("month", "2024-05") not in remaining


def test_threshold_grows_with_library_size():
    assert [moments.threshold_for(n) for n in (0, 1, 4, 8, 1024)] == [2, 2, 3, 4, 11]


# --- legacy -------------------------------------------------------------------------------------

def make_legacy_db(path: Path, sha: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, original_name TEXT, new_name TEXT, category TEXT, brand TEXT, "
                 "model TEXT, shot_type TEXT, primary_colors TEXT, description TEXT, tags TEXT, sha256 TEXT)")
    conn.execute("INSERT INTO photos VALUES (1,'Padova 0001.jpg','0001_ducati.jpg','Moto Sportiva','Ducati','998s','Stand',"
                 "'Rosso','Ducati rossa da competizione.','ducati, superbike, stand',?)", (sha,))
    conn.execute("INSERT INTO photos VALUES (2,'Padova 0002.jpg','x.jpg','Moto','Vespa','','', '', 'Altra','vespa',?)", ("0" * 64,))
    conn.commit()
    conn.close()


def test_legacy_import_matches_by_sha256_and_marks_data_manual(ctx, library, tmp_path):
    make_jpeg(library / "qualunque.jpg", color=(210, 20, 20))
    index_roots(ctx)
    sha = hash_file(library / "qualunque.jpg").sha256
    db = tmp_path / "legacy.db"
    make_legacy_db(db, sha)
    stats = legacy.import_legacy(ctx, db)
    assert (stats.total, stats.matched, stats.unmatched) == (2, 1, ["Padova 0002.jpg"])
    row = ctx.db.one("SELECT * FROM photos")
    assert row["title"] == "Ducati 998s" and row["title_src"] == sources.MANUAL
    assert row["caption"] == "Ducati rossa da competizione." and row["caption_src"] == sources.MANUAL
    assert {r["name"] for r in ctx.db.all("SELECT l.name FROM labels l")} == {"Moto Sportiva", "Ducati", "998s", "Stand"}
    assert {r["word"] for r in ctx.db.all("SELECT word FROM keywords")} == {"ducati", "superbike", "stand"}
    assert ctx.db.scalar("SELECT photo_count FROM albums WHERE title='Padova Moto Days 2026'") == 1
    assert [p["uid"] for p in query.search(ctx, "label:ducati").photos] == [row["uid"]]
    legacy.import_legacy(ctx, db)  # secondo passaggio: nessun duplicato
    assert ctx.db.scalar("SELECT COUNT(*) FROM albums") == 1 and ctx.db.scalar("SELECT COUNT(*) FROM photos_albums") == 1


def test_legacy_import_rejects_wrong_files(ctx, tmp_path):
    with pytest.raises(legacy.LegacyError):
        legacy.import_legacy(ctx, tmp_path / "manca.db")
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.commit()
    with pytest.raises(legacy.LegacyError):
        legacy.import_legacy(ctx, other)


@pytest.mark.skipif(not (ROOT / "_legacy/database/padova_moto_days.db").exists(), reason="catalogo legacy assente")
def test_real_legacy_catalog_is_readable_and_untouched(ctx):
    db = ROOT / "_legacy/database/padova_moto_days.db"
    before = db.stat().st_mtime_ns
    stats = legacy.import_legacy(ctx, db)
    assert stats.total == 334 and stats.matched == 0 and len(stats.unmatched) == 334
    assert db.stat().st_mtime_ns == before


# --- export -----------------------------------------------------------------------------------------

def test_export_json_and_csv(ctx, tmp_path):
    conn = ctx.db.conn()
    visible = photo(ctx, "vis", title="Ducati, rossa")
    photo(ctx, "priv", private=1)
    tags.add_keywords(conn, visible, ["uno", "due"], 16)
    assert export.export_catalog(ctx, "json", tmp_path / "out" / "c.json") == 1
    data = json.loads((tmp_path / "out" / "c.json").read_text(encoding="utf-8"))
    assert data[0]["uid"] == "vis" and data[0]["keywords"] == ["due", "uno"] and "embedding" not in data[0]
    assert export.export_catalog(ctx, "json", tmp_path / "all.json", include_private=True) == 2
    assert export.export_catalog(ctx, "csv", tmp_path / "c.csv") == 1
    raw = (tmp_path / "c.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader((tmp_path / "c.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["title"] == "Ducati, rossa" and rows[0]["keywords"] == "due; uno"
    with pytest.raises(ValueError):
        export.export_catalog(ctx, "xml", tmp_path / "c.xml")


# --- import di cartelle -----------------------------------------------------------------------------

@pytest.fixture()
def inbox(tmp_path) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def test_import_copies_with_date_and_crc_name_and_indexes(ctx, library, inbox):
    make_jpeg(inbox / "IMG_20230501_123456.jpg", color=(200, 10, 10))
    (inbox / "IMG_20230501_123456.jpg.xmp").write_text("<x/>", encoding="utf-8")
    stats = import_files(ctx, inbox)
    assert (stats.imported, stats.skipped, stats.errors) == (1, 0, []) and len(stats.photo_ids) == 1
    jpgs = list((library / "2023" / "05").glob("20230501_123456_*.jpg"))
    assert len(jpgs) == 1 and (library / "2023" / "05" / (jpgs[0].name + ".xmp")).exists()
    assert (inbox / "IMG_20230501_123456.jpg").exists()                    # copia: la sorgente resta
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1 and ctx.db.scalar("SELECT COUNT(*) FROM files") == 2


def test_import_skips_files_already_in_the_library(ctx, library, inbox):
    make_jpeg(inbox / "a.jpg", color=(1, 2, 3))
    assert import_files(ctx, inbox).imported == 1
    again = import_files(ctx, inbox)
    assert (again.imported, again.skipped) == (0, 1) and ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1


def test_import_move_removes_sources_after_success(ctx, library, inbox):
    make_jpeg(inbox / "a.jpg", color=(1, 2, 3))
    (inbox / "a.jpg.xmp").write_text("<x/>", encoding="utf-8")
    stats = import_files(ctx, inbox, move=True)
    assert stats.imported == 1 and stats.removed_sources == 2 and not list(inbox.iterdir())
    make_jpeg(inbox / "dup.jpg", color=(1, 2, 3))  # stesso contenuto già in libreria: la sorgente ridondante sparisce
    again = import_files(ctx, inbox, move=True)
    assert again.skipped == 1 and not (inbox / "dup.jpg").exists()


def test_import_never_overwrites_a_different_file_with_the_same_name(ctx, library, inbox):
    from PIL import Image

    from mosaic.organize.importer import sha1_and_crc

    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2024:01:02 03:04:05"
    other = inbox / "b.jpg"
    Image.new("RGB", (64, 48), (200, 100, 50)).save(other, "JPEG", exif=exif)
    _, crc = sha1_and_crc(other)
    folder = library / "2024" / "01"
    folder.mkdir(parents=True)
    blocker = folder / f"20240102_030405_{crc}.jpg"   # il nome che l'import sceglierebbe, ma con altro contenuto
    blocker.write_bytes(b"non toccare")
    stats = import_files(ctx, inbox)
    assert stats.imported == 1 and stats.errors == []
    assert blocker.read_bytes() == b"non toccare"
    assert len(list(folder.glob(f"20240102_030405_{crc}.00001.jpg"))) == 1


def test_import_rejects_bad_setups(ctx, library, tmp_path):
    with pytest.raises(ImportFailed):
        import_files(ctx, tmp_path / "nope")
    with pytest.raises(ImportFailed):
        import_files(ctx, library)
    nested = library / "dentro"
    nested.mkdir()
    with pytest.raises(ImportFailed):
        import_files(ctx, nested)
    ctx.cfg = replace(ctx.cfg, library_roots=())
    with pytest.raises(ImportFailed):
        import_files(ctx, tmp_path)


# --- pulizia ------------------------------------------------------------------------------------------

def test_cleanup_marks_restores_and_purges(ctx, library):
    make_jpeg(library / "a.jpg", color=(1, 1, 1))
    make_jpeg(library / "b.jpg", color=(2, 2, 2))
    index_roots(ctx)
    (library / "a.jpg").rename(library / "a.hidden")
    stats = cleanup.cleanup(ctx)
    assert stats.marked_missing == 1 and ctx.db.scalar("SELECT COUNT(*) FROM files WHERE missing=1") == 1
    (library / "a.hidden").rename(library / "a.jpg")
    assert cleanup.cleanup(ctx).restored == 1
    (library / "a.jpg").unlink()
    cleanup.cleanup(ctx)
    keep = ctx.db.scalar("SELECT sha1 FROM files WHERE name='b.jpg'")
    for folder, name in ((ctx.thumbs_dir, f"{keep}_224x224_tile.jpg"), (ctx.sidecar_dir, "ffff.jpg"), (ctx.exif_dir, f"{keep}.json")):
        (folder / "x").mkdir(parents=True, exist_ok=True)
        (folder / "x" / name).write_bytes(b"1")
    purged = cleanup.cleanup(ctx, do_purge=True)
    assert (purged.purged_files, purged.purged_photos, purged.cache_removed) == (1, 1, 1)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1 and (ctx.thumbs_dir / "x" / f"{keep}_224x224_tile.jpg").exists()


# --- watcher ----------------------------------------------------------------------------------------

def test_watcher_debounces_and_ignores_irrelevant_files(tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    fired = threading.Event()
    calls = []
    watcher = LibraryWatcher([str(folder)], 0.6, lambda: (calls.append(1), fired.set()))
    watcher.start()
    try:
        time.sleep(0.3)
        (folder / "note.txt").write_text("x")
        (folder / "x.jpg.part").write_text("x")
        assert not fired.wait(1.2)
        for i in range(4):
            make_jpeg(folder / f"p{i}.jpg")
            time.sleep(0.1)
        assert fired.wait(5)
        time.sleep(1.0)
        assert len(calls) == 1
    finally:
        watcher.stop()


def test_watcher_disabled_with_zero_delay(tmp_path):
    watcher = LibraryWatcher([str(tmp_path)], 0, lambda: None)
    watcher.start()
    assert watcher._observer is None
    watcher.stop()


def test_watcher_survives_failing_trigger(tmp_path):
    done = threading.Event()

    def boom():
        done.set()
        raise RuntimeError("x")

    watcher = LibraryWatcher([str(tmp_path)], 0.1, boom)
    watcher.notify()
    assert done.wait(2)
    watcher.stop()


# --- persone ---------------------------------------------------------------------------------------------

def add_marker(ctx, pid, uid, face_id=None):
    fid = ctx.db.scalar("SELECT id FROM files WHERE photo_id=?", (pid,))
    return ctx.db.conn().execute(
        "INSERT INTO markers(uid, file_id, photo_id, face_id, x, y, w, h) VALUES (?,?,?,?,0,0,.1,.1)", (uid, fid, pid, face_id)).lastrowid


def test_people_actions(ctx):
    conn = ctx.db.conn()
    p1, p2 = photo(ctx, "a"), photo(ctx, "b")
    conn.execute("INSERT INTO faces(id, embedding, created_at) VALUES ('f1', x'00', 'x')")
    add_marker(ctx, p1, "m1", "f1")
    add_marker(ctx, p2, "m2", "f1")
    add_marker(ctx, p2, "m3")
    subject = people.name_face(conn, "f1", "Anna Rossi")
    assert {r["name"] for r in conn.execute("SELECT name FROM markers WHERE face_id='f1'")} == {"Anna Rossi"}
    assert conn.execute("SELECT photo_count, marker_count FROM subjects WHERE id=?", (subject,)).fetchone()[:] == (2, 2)
    other = people.name_marker(conn, "m3", "Marco")
    with pytest.raises(people.PeopleError):
        people.rename_subject(conn, other, "Anna Rossi")
    people.rename_subject(conn, other, "Marco Bianchi")
    assert conn.execute("SELECT name FROM markers WHERE uid='m3'").fetchone()[0] == "Marco Bianchi"
    people.merge_subjects(conn, subject, other)
    assert conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 1
    assert conn.execute("SELECT marker_count FROM subjects").fetchone()[0] == 3
    people.reject_marker(conn, "m1")
    assert conn.execute("SELECT invalid, subj_id FROM markers WHERE uid='m1'").fetchone()[:] == (1, None)
    for fn, args in ((people.name_face, ("nessuno", "x")), (people.name_marker, ("zzz", "x")), (people.reject_marker, ("zzz",))):
        with pytest.raises(people.PeopleError):
            fn(conn, *args)
    with pytest.raises(people.PeopleError):
        people.rename_subject(conn, subject, "  ")
    with pytest.raises(people.PeopleError):
        people.merge_subjects(conn, subject, 9999)
    with pytest.raises(ValueError):
        people.ensure_subject(conn, " ")


def test_root_folder_becomes_an_album_even_when_photos_sit_directly_in_it(ctx):
    conn = ctx.db.conn()
    conn.execute("INSERT INTO folders(root, path) VALUES ('D:\\144___09', '')")
    for i in range(3):
        photo(ctx, f"r{i}")
    conn.execute("UPDATE photos SET folder_id=(SELECT id FROM folders WHERE path='')")
    moments.generate(ctx)
    row = conn.execute("SELECT * FROM albums WHERE type='folder'").fetchone()
    assert row["title"] == "144___09" and row["photo_count"] == 3 and row["filter"].startswith("root:")
    albums.update_album(conn, row["id"], description="Giornata al circuito.")
    moments.generate(ctx)
    kept = conn.execute("SELECT description, photo_count FROM albums WHERE id=?", (row["id"],)).fetchone()
    assert kept["description"] == "Giornata al circuito." and kept["photo_count"] == 3


def test_root_filter_selects_a_whole_root_with_its_subfolders(ctx):
    conn = ctx.db.conn()
    conn.execute("INSERT INTO folders(root, path) VALUES ('A', '')")
    conn.execute("INSERT INTO folders(root, path) VALUES ('A', 'sub')")
    conn.execute("INSERT INTO folders(root, path) VALUES ('B', '')")
    for uid, fid in (("a1", 1), ("a2", 2), ("b1", 3)):
        photo(ctx, uid)
        conn.execute("UPDATE photos SET folder_id=? WHERE uid=?", (fid, uid))
    found = {p["uid"] for p in query.search(ctx, "root:A").photos}
    assert found == {"a1", "a2"}
    assert {p["uid"] for p in query.search(ctx, "radice:B").photos} == {"b1"}
    assert {p["uid"] for p in query.search(ctx, "-root:A").photos} == {"b1"}
