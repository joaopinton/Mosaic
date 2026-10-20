"""Modo «Sincronizzata con il computer» (D-24): i file cancellati dal computer escono anche da Mosaic, con le cautele."""

import shutil
from dataclasses import replace

from conftest import make_jpeg

from mosaic.index.indexer import index_roots
from mosaic.organize import catalog


def library_of(ctx, library, n=4):
    for i in range(n):
        make_jpeg(library / f"f{i}.jpg", color=(i * 40, 10, 200 - i * 30))
    index_roots(ctx)
    return {r["name"]: r["photo_id"] for r in ctx.db.all("SELECT name, photo_id FROM files")}


def test_files_deleted_from_the_computer_leave_the_catalog(ctx, library):
    ids = library_of(ctx, library)
    (library / "f1.jpg").unlink()
    assert index_roots(ctx).missing == 1
    stats = catalog.sync_deleted(ctx)
    assert (stats.files, stats.photos, stats.skipped_roots) == (1, 1, [])
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos WHERE id=?", (ids["f1.jpg"],)) == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 3


def test_an_unplugged_drive_never_empties_the_catalog(ctx, library):
    library_of(ctx, library)
    ctx.db.conn().execute("UPDATE files SET missing=1")
    shutil.rmtree(library)  # la cartella non c'è più: disco staccato, rete assente
    stats = catalog.sync_deleted(ctx)
    assert stats.photos == 0 and stats.skipped_roots == [str(library)]
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 4


def test_too_many_missing_at_once_is_not_trusted(ctx, library, monkeypatch):
    library_of(ctx, library)
    monkeypatch.setattr(catalog, "SYNC_MIN_GUARD", 2)
    for name in ("f0.jpg", "f1.jpg", "f2.jpg"):
        (library / name).unlink()
    index_roots(ctx)
    assert catalog.sync_deleted(ctx).photos == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 4


def test_index_job_syncs_only_in_synced_mode(ctx, library):
    from mosaic.jobs.handlers import h_index
    from mosaic.jobs.queue import Job

    class Ctl:
        cancel = type("E", (), {"is_set": staticmethod(lambda: False)})()
        messages: list[str] = []

        def progress(self, value, message="", *_counts):
            self.messages.append(message)

        def submit(self, *a, **k):
            return 0

    library_of(ctx, library)
    (library / "f2.jpg").unlink()
    h_index(ctx, Job(1, "index"), Ctl())
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 4  # «Solo interfaccia»: resta come mancante
    ctx.cfg = replace(ctx.cfg, delete_mode="trash")
    ctl = Ctl()
    h_index(ctx, Job(2, "index", {"rescan": True}), ctl)
    ctx.db.conn().execute("UPDATE files SET missing=1 WHERE name='f2.jpg'")  # già segnato: un nuovo giro lo toglie
    ctl2 = Ctl()
    ctl2.messages = []
    (library / "f3.jpg").unlink()
    h_index(ctx, Job(3, "index"), ctl2)
    assert ctx.db.scalar("SELECT COUNT(*) FROM files WHERE name IN ('f2.jpg','f3.jpg')") == 0
    assert "cancellate dal computer" in ctl2.messages[-1]


def test_a_vanished_subfolder_is_not_a_deletion(ctx, library):
    for i in range(3):
        make_jpeg(library / f"top{i}.jpg", color=(i * 50, 20, 20))
    make_jpeg(library / "sotto" / "a.jpg", color=(10, 200, 10))
    index_roots(ctx)
    ctx.db.conn().execute("UPDATE files SET missing=1 WHERE dir='sotto'")
    shutil.rmtree(library / "sotto")  # come una sottocartella di rete illeggibile per un attimo
    assert catalog.sync_deleted(ctx).photos == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 4


def test_a_root_with_every_file_missing_is_never_synced(ctx, library):
    make_jpeg(library / "solo.jpg")
    index_roots(ctx)
    (library / "solo.jpg").unlink()
    index_roots(ctx)
    stats = catalog.sync_deleted(ctx)
    assert stats.photos == 0 and stats.skipped_roots == [str(library)]
