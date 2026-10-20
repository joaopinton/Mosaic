"""Solo volti in primo piano (D-22): la folla sullo sfondo non viene riconosciuta né raggruppata."""

from pathlib import Path

import numpy as np

from mosaic.ai.face_worker import prune_background
from mosaic.ai.faces import foreground
from mosaic.ai.registry import face_spec
from mosaic.service import Mosaic

SPEC = face_spec(Path("models"))


def test_people_posing_together_are_all_kept():
    keep = foreground(np.array([300, 280, 260]), 3000, np.array([95, 92, 90]), SPEC)
    assert keep.tolist() == [True, True, True]


def test_crowd_behind_the_subject_is_dropped():
    # soggetto alto il 15% del lato corto, gente dietro al 4-6%
    keep = foreground(np.array([450, 180, 120, 150]), 3000, np.array([95, 91, 90, 88]), SPEC)
    assert keep.tolist() == [True, False, False, False]


def test_a_crowd_shot_has_no_foreground_faces():
    keep = foreground(np.array([90, 100, 110]), 3000, np.array([95, 95, 95]), SPEC)
    assert not keep.any()


def test_a_large_false_detection_does_not_hide_real_faces():
    # un bicchiere rilevato come volto enorme ma con punteggio basso
    keep = foreground(np.array([1200, 300, 290]), 3000, np.array([70, 93, 92]), SPEC)
    assert keep.tolist() == [True, True, True]


def test_empty_or_sizeless_input():
    assert foreground(np.array([]), 3000, np.array([]), SPEC).size == 0
    assert not foreground(np.array([100.0]), 0, np.array([95]), SPEC).any()


def photo(conn, width=4000, height=3000) -> tuple[int, int]:
    pid = conn.execute("INSERT INTO photos(uid, width, height, created_at, updated_at) VALUES (?, ?, ?, 'x', 'x')",
                       (f"p{width}{height}{conn.total_changes}", width, height)).lastrowid
    fid = conn.execute("INSERT INTO files(photo_id, root, name, original_name, media_type) VALUES (?, 'r', ?, 'n', 'image')",
                       (pid, f"f{pid}.jpg")).lastrowid
    return pid, fid


def face(conn, pid, fid, h, *, src=12, score=92, name="") -> int:
    return conn.execute(
        "INSERT INTO markers(uid, file_id, photo_id, src, name, x, y, w, h, score) VALUES (?,?,?,?,?,0.1,0.1,?,?,?)",
        (f"m{conn.total_changes}", fid, pid, src, name, h * 0.75, h, score)).lastrowid


def test_prune_removes_automatic_background_faces_but_keeps_manual_ones(ctx):
    conn = ctx.db.conn()
    pid, fid = photo(conn)
    main = face(conn, pid, fid, 0.20)
    crowd = face(conn, pid, fid, 0.05)
    named = face(conn, pid, fid, 0.04, src=64, name="Anna")
    sizeless, sfid = photo(conn, 0, 0)
    unknown = face(conn, sizeless, sfid, 0.01)
    with ctx.db.tx() as tx:
        assert prune_background(tx, SPEC) == 1
    left = {r[0] for r in conn.execute("SELECT id FROM markers")}
    assert left == {main, named, unknown} and crowd not in left


def test_startup_prunes_old_libraries_and_regroups_faces(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_CONFIG", str(tmp_path / "config.yaml"))
    conn = ctx.db.conn()
    pid, fid = photo(conn)
    face(conn, pid, fid, 0.25)
    face(conn, pid, fid, 0.03)
    m = Mosaic(ctx)
    try:
        m._prune_background_faces()
        assert ctx.db.scalar("SELECT COUNT(*) FROM markers") == 1
        assert ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE type='faces'") == 1
        m._prune_background_faces()  # niente da togliere: nessun altro job
        assert ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE type='faces'") == 1
    finally:
        m.runner.stop()
