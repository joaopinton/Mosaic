import hashlib
import os
from dataclasses import replace
from pathlib import Path

from conftest import make_jpeg

from mosaic.config import RenameConfig
from mosaic.index.indexer import index_roots
from mosaic.meta import sources
from mosaic.organize import rename, tags


def make_stack(library: Path, base: str, color=(200, 30, 30)) -> list[str]:
    make_jpeg(library / f"{base}.jpg", color=color)
    (library / f"{base}.CR2").write_bytes(b"II*\x00" + base.encode() * 8)
    (library / f"{base}.jpg.xmp").write_text(f"<x>{base}</x>", encoding="utf-8")
    (library / f"{base}.mov").write_bytes(b"\x00\x00\x00\x18ftypmp42" + base.encode() * 4)
    return [f"{base}.jpg", f"{base}.CR2", f"{base}.jpg.xmp", f"{base}.mov"]


def snapshot(folder: Path) -> dict[str, tuple[str, float]]:
    return {p.name: (hashlib.sha1(p.read_bytes()).hexdigest(), p.stat().st_mtime) for p in folder.iterdir() if p.is_file()}


def set_title(ctx, pid, title):
    sources.set_text(ctx.db.conn(), pid, "title", title, sources.MANUAL)


def names(ctx):
    return sorted(r["name"] for r in ctx.db.all("SELECT name FROM files"))


def test_whole_stack_is_renamed_together(ctx, library):
    make_stack(library, "IMG_1")
    (pid,) = index_roots(ctx).changed_photo_ids
    set_title(ctx, pid, "Ducati Panigale rossa")
    plan = rename.plan_rename(ctx)
    assert plan.photos == 1 and len(plan.ops) == 4
    result = rename.apply_plan(ctx, plan)
    assert (result.photos, result.files, result.failed) == (1, 4, [])
    expected = ["0001_ducati-panigale-rossa.CR2", "0001_ducati-panigale-rossa.jpg",
                "0001_ducati-panigale-rossa.jpg.xmp", "0001_ducati-panigale-rossa.mov"]
    assert sorted(p.name for p in library.iterdir()) == expected and names(ctx) == expected
    assert ctx.db.scalar("SELECT seq FROM photos") == 1
    assert ctx.db.scalar("SELECT COUNT(DISTINCT original_name) FROM files WHERE original_name LIKE 'IMG_1%'") == 4


def test_dry_run_changes_nothing(ctx, library):
    make_stack(library, "IMG_1")
    index_roots(ctx)
    before = snapshot(library)
    plan = rename.plan_rename(ctx)
    assert plan.ops and snapshot(library) == before and ctx.db.scalar("SELECT COUNT(*) FROM rename_journal") == 0


def test_reindex_after_rename_recognizes_files_and_creates_nothing_new(ctx, library):
    make_stack(library, "IMG_1")
    (pid,) = index_roots(ctx).changed_photo_ids
    set_title(ctx, pid, "Moto")
    rename.apply_plan(ctx, rename.plan_rename(ctx))
    stats = index_roots(ctx)
    assert stats.new_photos == 0 and stats.new_files == 0 and stats.moved == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1 and ctx.db.scalar("SELECT COUNT(*) FROM files") == 4


def test_rollback_restores_names_content_and_mtime_exactly(ctx, library):
    names_before = make_stack(library, "IMG_1") + make_stack(library, "IMG_2", color=(1, 2, 3))
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        set_title(ctx, pid, f"foto {pid}")
    before = snapshot(library)
    result = rename.apply_plan(ctx, rename.plan_rename(ctx))
    assert result.files == 8 and set(snapshot(library)) != set(names_before)
    back = rename.rollback(ctx)
    assert back.files == 8 and back.photos == 2 and back.failed == []
    assert snapshot(library) == before
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos WHERE seq IS NOT NULL") == 0
    stats = index_roots(ctx)
    marks = ",".join("?" * len(names_before))
    assert stats.new_photos == 0
    assert ctx.db.scalar(f"SELECT COUNT(*) FROM files WHERE name IN ({marks})", tuple(names_before)) == 8


def test_rollback_of_a_single_batch(ctx, library):
    make_stack(library, "A")
    make_stack(library, "B", color=(9, 9, 9))
    first, second = index_roots(ctx).changed_photo_ids
    set_title(ctx, first, "uno")
    set_title(ctx, second, "due")
    b1 = rename.apply_plan(ctx, rename.plan_rename(ctx, [first])).batch_id
    b2 = rename.apply_plan(ctx, rename.plan_rename(ctx, [second])).batch_id
    assert b1 != b2
    rename.rollback(ctx, b2)
    assert ctx.db.scalar("SELECT seq FROM photos WHERE id=?", (first,)) == 1
    assert ctx.db.scalar("SELECT seq FROM photos WHERE id=?", (second,)) is None
    assert (library / "A.jpg").exists() is False and (library / "B.jpg").exists()


def test_name_collision_gets_numeric_suffix_and_never_overwrites(ctx, library):
    make_jpeg(library / "a.jpg", color=(1, 1, 1))
    make_jpeg(library / "b.jpg", color=(2, 2, 2))
    make_jpeg(library / "tramonto.jpg", color=(3, 3, 3))  # file esistente con il nome di destinazione
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        set_title(ctx, pid, "Tramonto")
    ctx.cfg = replace(ctx.cfg, rename=RenameConfig(pattern="{slug}"))
    original = (library / "tramonto.jpg").read_bytes()
    rename.apply_plan(ctx, rename.plan_rename(ctx))
    final = sorted(p.name for p in library.iterdir())
    assert len(final) == 3 and len(set(final)) == 3
    assert "tramonto.jpg" in final and (library / "tramonto.jpg").read_bytes() == original
    assert sum(".00001." in n or ".00002." in n for n in final) == 2


def test_slug_sources_and_sanitizing(ctx, library):
    for i, n in enumerate(("a.jpg", "b.jpg", "c.jpg")):
        make_jpeg(library / n, color=(40 * (i + 1), 5, 5))
    a, b, c = index_roots(ctx).changed_photo_ids
    set_title(ctx, a, 'Città: "Moto" / Panigale?* è fantastica')
    lid = tags.get_or_create_label(ctx.db.conn(), "Vespa")
    tags.set_photo_label(ctx.db.conn(), b, lid, 5, sources.ONNX)
    ctx.db.conn().execute("UPDATE photos SET taken_at_local='2024-03-05T10:00:00' WHERE id=?", (c,))
    rename.apply_plan(ctx, rename.plan_rename(ctx))
    got = {r["original_name"]: r["name"] for r in ctx.db.all("SELECT name, original_name FROM files")}
    assert got["a.jpg"].endswith("citta-moto-panigale-e-fantastica.jpg")
    assert got["b.jpg"].endswith("_vespa.jpg")
    assert got["c.jpg"].endswith("_20240305.jpg")


def test_sanitize_rules():
    assert rename.sanitize('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"
    assert rename.sanitize("nome. ") == "nome" and rename.sanitize("CON") == "_CON"
    assert len(rename.sanitize("x" * 500)) <= rename.MAX_BASE


def test_date_pattern_and_sequences_continue_across_runs(ctx, library):
    make_jpeg(library / "a.jpg", color=(1, 1, 1))
    (a,) = index_roots(ctx).changed_photo_ids
    ctx.db.conn().execute("UPDATE photos SET taken_at_local='2024-03-05T10:00:00' WHERE id=?", (a,))
    ctx.cfg = replace(ctx.cfg, rename=RenameConfig(pattern="{date:%Y%m%d}_{seq:03}_{slug}"))
    rename.apply_plan(ctx, rename.plan_rename(ctx))
    make_jpeg(library / "b.jpg", color=(2, 2, 2))
    (b,) = index_roots(ctx).changed_photo_ids
    plan = rename.plan_rename(ctx)
    assert plan.seqs == {b: 2} and [op.photo_id for op in plan.ops] == [b]
    assert (library / "20240305_001_20240305.jpg").exists()


def test_already_renamed_photos_are_skipped_even_when_forced(ctx, library):
    make_jpeg(library / "a.jpg")
    (a,) = index_roots(ctx).changed_photo_ids
    set_title(ctx, a, "uno")
    rename.apply_plan(ctx, rename.plan_rename(ctx))
    assert rename.plan_rename(ctx).ops == []
    forced = rename.plan_rename(ctx, force=True)
    assert forced.ops == [] and forced.skipped == [(a, "già rinominata")]


def test_failure_in_the_middle_of_a_stack_reverts_that_photo(ctx, library, monkeypatch):
    make_stack(library, "IMG_1")
    (pid,) = index_roots(ctx).changed_photo_ids
    set_title(ctx, pid, "Moto")
    before = snapshot(library)
    real = rename._rename_on_disk

    def flaky(root, dir_, old, new):
        if old.endswith(".mov"):
            raise PermissionError("file in uso")
        real(root, dir_, old, new)

    monkeypatch.setattr(rename, "_rename_on_disk", flaky)
    result = rename.apply_plan(ctx, rename.plan_rename(ctx))
    assert result.photos == 0 and result.failed and "file in uso" in result.failed[0][1]
    assert snapshot(library) == before
    assert ctx.db.scalar("SELECT seq FROM photos") is None and sorted(names(ctx)) == sorted(before)
    states = {r["state"] for r in ctx.db.all("SELECT state FROM rename_journal")}
    assert states == {"rolled_back", "failed"}


def test_target_created_after_planning_is_never_overwritten(ctx, library):
    make_jpeg(library / "a.jpg", color=(1, 1, 1))
    (a,) = index_roots(ctx).changed_photo_ids
    set_title(ctx, a, "uno")
    plan = rename.plan_rename(ctx)
    victim = library / plan.ops[0].new_name
    victim.write_bytes(b"importante")
    result = rename.apply_plan(ctx, plan)
    assert result.failed and victim.read_bytes() == b"importante" and (library / "a.jpg").exists()


def test_recover_finalizes_interrupted_renames(ctx, library):
    make_jpeg(library / "a.jpg")
    make_jpeg(library / "b.jpg", color=(5, 5, 5))
    a, b = index_roots(ctx).changed_photo_ids
    fa, fb = (ctx.db.scalar("SELECT id FROM files WHERE photo_id=?", (p,)) for p in (a, b))
    conn = ctx.db.conn()
    for fid, pid, old, new in ((fa, a, "a.jpg", "x.jpg"), (fb, b, "b.jpg", "y.jpg")):
        conn.execute("INSERT INTO rename_journal(batch_id, file_id, photo_id, root, old_dir, old_name, new_dir, new_name, ts) "
                     "VALUES ('crash', ?, ?, ?, '', ?, '', ?, 'x')", (fid, pid, str(library.resolve()), old, new))
    os.rename(library / "a.jpg", library / "x.jpg")  # il crash è avvenuto dopo la prima sola rinomina
    assert rename.recover(ctx) == 2
    assert ctx.db.scalar("SELECT name FROM files WHERE id=?", (fa,)) == "x.jpg"
    assert ctx.db.scalar("SELECT name FROM files WHERE id=?", (fb,)) == "b.jpg"
    assert {r["state"] for r in ctx.db.all("SELECT state FROM rename_journal")} == {"done", "failed"}
    assert rename.recover(ctx) == 0


def test_auto_rename_respects_the_switch(ctx, library):
    make_jpeg(library / "a.jpg")
    index_roots(ctx)
    ctx.cfg = replace(ctx.cfg, rename=RenameConfig(auto=False))
    assert rename.auto_rename(ctx) is None and (library / "a.jpg").exists()
    ctx.cfg = replace(ctx.cfg, rename=RenameConfig(auto=True))
    assert rename.auto_rename(ctx).photos == 1 and not (library / "a.jpg").exists()


def test_missing_files_are_skipped_and_reported(ctx, library):
    make_jpeg(library / "a.jpg")
    (a,) = index_roots(ctx).changed_photo_ids
    (library / "a.jpg").unlink()
    index_roots(ctx)
    plan = rename.plan_rename(ctx)
    assert plan.ops == [] and plan.skipped == [(a, "nessun file presente")]
