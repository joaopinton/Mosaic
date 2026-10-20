import os
import shutil
import time
from pathlib import Path

from conftest import make_jpeg, make_png

from mosaic.index.discover import STORAGE_MARKER, iter_directories
from mosaic.index.indexer import index_roots


def counts(ctx):
    return (
        ctx.db.scalar("SELECT COUNT(*) FROM photos"),
        ctx.db.scalar("SELECT COUNT(*) FROM files"),
    )


def make_stack(library: Path, base: str = "IMG_1") -> None:
    make_jpeg(library / f"{base}.jpg")
    (library / f"{base}.CR2").write_bytes(b"II*\x00" + b"raw" * 10)
    (library / f"{base}.jpg.xmp").write_text("<x/>", encoding="utf-8")
    (library / f"{base}.mov").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 20)


def test_stack_becomes_one_photo_with_four_files(ctx, library):
    make_stack(library)
    stats = index_roots(ctx)
    assert counts(ctx) == (1, 4)
    assert stats.new_photos == 1 and stats.new_files == 4
    primary = ctx.db.one("SELECT name FROM files WHERE is_primary=1")
    assert primary["name"] == "IMG_1.jpg"
    assert ctx.db.scalar("SELECT COUNT(*) FROM folders") == 1


def test_second_run_skips_everything(ctx, library):
    make_stack(library)
    index_roots(ctx)
    stats = index_roots(ctx)
    assert stats.skipped == 4 and stats.new_photos == 0 and stats.changed_photo_ids == []


def test_rescan_reprocesses(ctx, library):
    make_jpeg(library / "a.jpg")
    index_roots(ctx)
    stats = index_roots(ctx, rescan=True)
    assert len(stats.changed_photo_ids) == 1 and counts(ctx) == (1, 1)


def test_modified_sidecar_requeues_main_file(ctx, library):
    make_stack(library)
    index_roots(ctx)
    xmp = library / "IMG_1.jpg.xmp"
    xmp.write_text("<x>changed and longer</x>", encoding="utf-8")
    os.utime(xmp, (time.time() + 10, time.time() + 10))
    stats = index_roots(ctx)
    assert len(stats.changed_photo_ids) == 1
    assert ctx.db.scalar("SELECT meta_at FROM photos") is None


def test_duplicate_copy_goes_to_duplicates_table(ctx, library):
    make_jpeg(library / "a.jpg")
    (library / "sub").mkdir()
    shutil.copy(library / "a.jpg", library / "sub" / "copy.jpg")
    stats = index_roots(ctx)
    assert counts(ctx) == (1, 1)
    assert stats.duplicates == 1
    dup = ctx.db.one("SELECT * FROM duplicates")
    assert dup["name"] in ("a.jpg", "copy.jpg")


def test_moved_file_keeps_photo_and_metadata(ctx, library):
    make_jpeg(library / "a.jpg")
    index_roots(ctx)
    ctx.db.conn().execute("UPDATE photos SET title='Mio titolo', title_src=64")
    (library / "new").mkdir()
    shutil.move(str(library / "a.jpg"), str(library / "new" / "b.jpg"))
    stats = index_roots(ctx)
    assert stats.moved == 1 and stats.new_photos == 0
    assert counts(ctx) == (1, 1)
    row = ctx.db.one("SELECT f.dir, f.name, f.original_name, p.title FROM files f JOIN photos p ON p.id=f.photo_id")
    assert (row["dir"], row["name"], row["original_name"], row["title"]) == ("new", "b.jpg", "a.jpg", "Mio titolo")


def test_deleted_file_is_marked_missing(ctx, library):
    make_jpeg(library / "a.jpg")
    make_jpeg(library / "b.jpg", color=(1, 2, 3))
    index_roots(ctx)
    (library / "a.jpg").unlink()
    stats = index_roots(ctx)
    assert stats.missing == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM files WHERE missing=1") == 1


def test_wrong_extension_is_logged_and_skipped(ctx, library):
    (library / "fake.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    stats = index_roots(ctx)
    assert counts(ctx) == (0, 0) and stats.errors == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors") == 1


def test_hidden_ignored_and_storage_dirs_are_skipped(library):
    make_jpeg(library / "ok.jpg")
    make_jpeg(library / ".hidden" / "x.jpg")
    make_jpeg(library / "skipme" / "y.jpg")
    (library / ".mosaicignore").write_text("skipme\n*.tmp.jpg\n", encoding="utf-8")
    make_jpeg(library / "z.tmp.jpg")
    make_jpeg(library / "store" / "s.jpg")
    (library / "store" / STORAGE_MARKER).write_text("", encoding="utf-8")
    seen = [f.name for _, files in iter_directories(library) for f in files]
    assert seen == ["ok.jpg"]


def test_sequences_stack_when_enabled(ctx, library):
    from dataclasses import replace

    make_jpeg(library / "a.jpg")
    make_jpeg(library / "a (2).jpg", color=(9, 9, 9))
    ctx.cfg = replace(ctx.cfg, stack_sequences=True)
    index_roots(ctx)
    assert counts(ctx)[0] == 1


def test_png_and_nested_folders(ctx, library):
    make_png(library / "2024" / "05" / "p.png")
    make_jpeg(library / "2024" / "j.jpg")
    index_roots(ctx)
    assert counts(ctx) == (2, 2)
    assert ctx.db.scalar("SELECT COUNT(*) FROM folders") == 2


def test_cancel_stops_early(ctx, library):
    import threading

    make_jpeg(library / "a.jpg")
    event = threading.Event()
    event.set()
    stats = index_roots(ctx, cancel=event)
    assert stats.cancelled and counts(ctx) == (0, 0)
