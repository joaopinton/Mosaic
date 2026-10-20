import numpy as np

from mosaic.ai import face_worker
from mosaic.ai.registry import face_spec
from mosaic.organize.people import ensure_subject

rng = np.random.default_rng(7)
SPEC = face_spec(__import__("pathlib").Path("models"))
MODEL = SPEC.recognizer.id


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def person(seed: int) -> np.ndarray:
    return unit(np.random.default_rng(seed).normal(size=128))


def add_marker(ctx, vec, *, size=150, score=95, name="", file_id=None, photo_id=None) -> int:
    conn = ctx.db.conn()
    if photo_id is None:
        photo_id = conn.execute(
            "INSERT INTO photos(uid, created_at, updated_at) VALUES (?, 'x', 'x')", (f"p{rng.integers(1 << 40)}",)).lastrowid
        file_id = conn.execute(
            "INSERT INTO files(photo_id, root, name, original_name, media_type) VALUES (?, 'r', ?, 'n', 'image')",
            (photo_id, f"f{photo_id}.jpg")).lastrowid
    return conn.execute(
        "INSERT INTO markers(uid, file_id, photo_id, src, name, embedding, embed_model, x, y, w, h, size, score) "
        "VALUES (?,?,?,12,?,?,?,0.1,0.1,0.2,0.2,?,?)",
        (f"m{rng.integers(1 << 40)}", file_id, photo_id, name, vec.astype(np.float32).tobytes(), MODEL, size, score)).lastrowid


def noisy(base, n, noise=0.015):
    return [unit(base + rng.normal(0, noise, size=128)) for _ in range(n)]


def marker(ctx, mid):
    return ctx.db.one("SELECT * FROM markers WHERE id=?", (mid,))


def test_clusters_are_created_for_groups_of_at_least_three(ctx):
    a, b = person(1), person(2)
    ids_a = [add_marker(ctx, v) for v in noisy(a, 8)]
    ids_b = [add_marker(ctx, v) for v in noisy(b, 6)]
    lonely = add_marker(ctx, person(3))
    stats = face_worker.run_faces(ctx)
    assert stats.clusters == 2
    assert ctx.db.scalar("SELECT COUNT(*) FROM faces") == 2
    assert len({marker(ctx, m)["face_id"] for m in ids_a}) == 1
    assert marker(ctx, ids_a[0])["face_id"] != marker(ctx, ids_b[0])["face_id"]
    assert marker(ctx, lonely)["face_id"] is None


def test_small_or_low_score_faces_do_not_seed_clusters(ctx):
    base = person(4)
    for v in noisy(base, 6):
        add_marker(ctx, v, size=60)         # sotto 112 px
    for v in noisy(person(5), 6):
        add_marker(ctx, v, score=70)        # sotto il punteggio di clustering (85)
    assert face_worker.run_faces(ctx).clusters == 0


def test_new_faces_match_existing_cluster_without_creating_a_new_one(ctx):
    base = person(6)
    for v in noisy(base, 7):
        add_marker(ctx, v)
    face_worker.run_faces(ctx)
    newcomer = add_marker(ctx, noisy(base, 1)[0], size=40, score=60)  # piccolo, ma riconoscibile
    stats = face_worker.run_faces(ctx)
    assert stats.clusters == 0 and stats.matched >= 1
    assert marker(ctx, newcomer)["face_id"] is not None
    assert ctx.db.scalar("SELECT COUNT(*) FROM faces") == 1


def test_name_given_to_one_face_is_propagated_by_consensus(ctx):
    base = person(8)
    ids = [add_marker(ctx, v) for v in noisy(base, 6)]
    ctx.db.conn().execute("UPDATE markers SET name='Anna' WHERE id=?", (ids[0],))
    stats = face_worker.run_faces(ctx)
    assert stats.named == 1 and stats.consensus == 1
    assert {marker(ctx, i)["name"] for i in ids} == {"Anna"}
    subj = ctx.db.one("SELECT * FROM subjects WHERE name='Anna'")
    assert subj["marker_count"] == 6 and subj["photo_count"] == 6


def test_collision_unassigns_conflicting_markers_and_narrows_radius(ctx):
    base = person(9)
    ids = [add_marker(ctx, v) for v in noisy(base, 8)]
    face_worker.run_faces(ctx)
    conn = ctx.db.conn()
    anna, marco = ensure_subject(conn, "Anna"), ensure_subject(conn, "Marco")
    face_id = marker(ctx, ids[0])["face_id"]
    conn.execute("UPDATE faces SET subj_id=? WHERE id=?", (anna, face_id))
    marks = ",".join("?" * 7)
    conn.execute(f"UPDATE markers SET subj_id=?, name='Anna' WHERE id IN ({marks})", (anna, *ids[:7]))
    conn.execute("UPDATE markers SET subj_id=?, name='Marco', face_dist=0.2 WHERE id=?", (marco, ids[7]))
    stats = face_worker.run_faces(ctx)
    assert stats.collisions == 1
    assert ctx.db.scalar("SELECT collision_radius FROM faces WHERE id=?", (face_id,)) in (0, 0.0) or \
        ctx.db.scalar("SELECT collision_radius FROM faces WHERE id=?", (face_id,)) >= 0.05
    assert marker(ctx, ids[7])["subj_id"] == marco   # la persona resta quella indicata dall'utente


def test_clusters_of_the_same_person_are_merged(ctx):
    base_a, base_b = person(10), person(11)
    for v in noisy(base_a, 6):
        add_marker(ctx, v)
    for v in noisy(base_b, 6):
        add_marker(ctx, v)
    face_worker.run_faces(ctx)
    conn = ctx.db.conn()
    subj = ensure_subject(conn, "Luca")
    conn.execute("UPDATE faces SET subj_id=?", (subj,))
    # avvicino un cluster all'altro: stessa persona con due cluster vicini
    faces = conn.execute("SELECT id FROM faces ORDER BY id").fetchall()
    first = np.frombuffer(conn.execute("SELECT embedding FROM faces WHERE id=?", (faces[0]["id"],)).fetchone()[0], dtype=np.float32)
    near = unit(first + rng.normal(0, 0.01, size=128)).tobytes()
    conn.execute("UPDATE faces SET embedding=? WHERE id=?", (near, faces[1]["id"]))
    stats = face_worker.run_faces(ctx)
    assert stats.merged == 1 and ctx.db.scalar("SELECT COUNT(*) FROM faces") == 1


def test_orphan_markers_of_missing_files_are_removed(ctx):
    mid = add_marker(ctx, person(12))
    ctx.db.conn().execute("UPDATE files SET missing=1")
    stats = face_worker.run_faces(ctx)
    assert stats.orphans == 1 and marker(ctx, mid) is None


def test_clusters_without_markers_are_deleted(ctx):
    for v in noisy(person(13), 6):
        add_marker(ctx, v)
    face_worker.run_faces(ctx)
    ctx.db.conn().execute("DELETE FROM markers")
    assert face_worker.run_faces(ctx).removed == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM faces") == 0


def test_markers_from_another_model_are_ignored(ctx):
    for v in noisy(person(14), 8):
        add_marker(ctx, v)
    ctx.db.conn().execute("UPDATE markers SET embed_model='altro-modello'")
    assert face_worker.run_faces(ctx).clusters == 0
