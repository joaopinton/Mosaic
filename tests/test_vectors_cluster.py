import numpy as np

from mosaic.ai import cluster, neardup
from mosaic.ai.cluster import FaceRef
from mosaic.ai.registry import RecognizerSpec
from mosaic.ai.vectors import VectorIndex, decode_vector, encode_vector, get_index, save_embeddings

SPEC = RecognizerSpec()
rng = np.random.default_rng(42)


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)


def blob(center: np.ndarray, n: int, noise: float) -> np.ndarray:
    return unit(center + rng.normal(0, noise, size=(n, center.size)))


def make_photo(ctx, uid: str) -> int:
    return ctx.db.conn().execute(
        "INSERT INTO photos(uid, created_at, updated_at) VALUES (?, '2024-01-01', '2024-01-01')", (uid,)).lastrowid


def test_vector_roundtrip_float16():
    v = unit(rng.normal(size=768))
    back = decode_vector(encode_vector(v))
    assert back.shape == (768,) and float(back @ v) > 0.9999


def test_search_orders_by_cosine_and_filters(ctx):
    vecs = unit(rng.normal(size=(50, 32)))
    ids = [make_photo(ctx, f"p{i}") for i in range(50)]
    save_embeddings(ctx.db.conn(), "m", ids, vecs)
    index = VectorIndex.load(ctx.db, "m")
    assert len(index) == 50
    found, scores = index.search(vecs[7], 5)
    assert found[0] == ids[7] and abs(scores[0] - 1) < 1e-2 and list(scores) == sorted(scores, reverse=True)
    only, _ = index.search(vecs[7], 5, allowed=ids[10:20])
    assert set(only.tolist()) <= set(ids[10:20]) and len(only) == 5
    assert index.search(vecs[7], 5, allowed=[])[0].size == 0
    assert VectorIndex.empty().search(vecs[0], 3)[0].size == 0


def test_deleted_photos_are_excluded_and_models_do_not_mix(ctx):
    vecs = unit(rng.normal(size=(3, 16)))
    ids = [make_photo(ctx, f"q{i}") for i in range(3)]
    save_embeddings(ctx.db.conn(), "A", ids, vecs)
    save_embeddings(ctx.db.conn(), "B", ids[:1], vecs[:1])
    ctx.db.conn().execute("UPDATE photos SET deleted_at='x' WHERE id=?", (ids[2],))
    assert len(VectorIndex.load(ctx.db, "A")) == 2
    assert len(VectorIndex.load(ctx.db, "B")) == 1


def test_shared_index_reloads_when_embeddings_change(ctx):
    ctx.cfg = type(ctx.cfg)(**{**ctx.cfg.__dict__, "ai": type(ctx.cfg.ai)(model="m")})
    first = make_photo(ctx, "a")
    save_embeddings(ctx.db.conn(), "m", [first], unit(rng.normal(size=(1, 8))))
    assert len(get_index(ctx)) == 1
    assert get_index(ctx) is get_index(ctx)
    second = make_photo(ctx, "b")
    save_embeddings(ctx.db.conn(), "m", [second], unit(rng.normal(size=(1, 8))))
    assert len(get_index(ctx)) == 2


def test_pairwise_above_finds_near_duplicates():
    base = unit(rng.normal(size=(1, 64)))
    twin = unit(base + rng.normal(0, 0.01, size=(1, 64)))
    other = unit(rng.normal(size=(3, 64)))
    index = VectorIndex(np.arange(5), np.vstack([base, twin, other]))
    pairs = index.pairwise_above(0.97)
    assert [(a, b) for a, b, _ in pairs] == [(0, 1)]
    assert neardup.group_pairs(pairs) == [[0, 1]]


def test_dbscan_finds_three_clusters_and_noise():
    centers = unit(rng.normal(size=(3, 128)))
    data = np.vstack([blob(c, 12, 0.02) for c in centers] + [unit(rng.normal(size=(4, 128)))])
    labels = cluster.dbscan(data, 0.72, 5)
    assert len(set(labels[:36].tolist())) == 3 and set(labels[36:]) == {-1}
    assert set(labels[:12]) != set(labels[12:24])


def test_dbscan_edge_cases():
    assert cluster.dbscan(np.zeros((0, 4)), 0.5, 3).size == 0
    assert (cluster.dbscan(unit(rng.normal(size=(2, 8))), 0.5, 5) == -1).all()


def test_cluster_faces_returns_centroid_and_bounded_radius():
    c = unit(rng.normal(size=128))
    groups = cluster.cluster_faces(blob(c, 20, 0.02), SPEC, 5)
    assert len(groups) == 1 and len(groups[0].members) == 20
    assert float(groups[0].centroid @ c) > 0.99 and 0 < groups[0].radius <= SPEC.cluster_radius
    assert cluster.face_id(groups[0].centroid) == cluster.face_id(groups[0].centroid.copy())


def test_too_wide_group_is_split_or_discarded():
    # due nuclei vicini (distanza ≈ 0,9 tra i centri) che DBSCAN con eps 0,72 unirebbe in un solo gruppo
    a = unit(rng.normal(size=128))
    b = unit(a + 0.9 * unit(rng.normal(size=128)))
    data = np.vstack([blob(a, 15, 0.01), blob(b, 15, 0.01)])
    groups = cluster.cluster_faces(data, SPEC, 5)
    limit = SPEC.cluster_radius + SPEC.cluster_slack
    assert all(float(np.linalg.norm(data[list(g.members)] - g.centroid, axis=1).max()) <= limit for g in groups)


def ref(vec, radius=0.4, subj=None, collision=0.0, fid="f"):
    return FaceRef(fid, vec, radius, collision, subj)


def test_match_accepts_within_limit_and_rejects_outside():
    c = unit(rng.normal(size=128))
    near = unit(c + rng.normal(0, 0.01, size=128))
    far = unit(rng.normal(size=128))
    assert cluster.match_face(near, [ref(c)], SPEC)[0].id == "f"
    assert cluster.match_face(far, [ref(c)], SPEC) is None
    assert cluster.match_face(near, [], SPEC) is None


def test_match_is_ambiguous_between_different_people_close_together():
    c = unit(rng.normal(size=128))
    twin = unit(c + rng.normal(0, 0.0003, size=128))   # distanza ≈ 0,003 dal primo cluster
    probe = unit(c + rng.normal(0, 0.0001, size=128))
    faces = [ref(c, subj=1, fid="a"), ref(twin, subj=2, fid="b")]
    assert cluster.match_face(probe, faces, SPEC) is None
    same_person = [ref(c, subj=1, fid="a"), ref(twin, subj=1, fid="b")]
    assert cluster.match_face(probe, same_person, SPEC) is not None


def test_collision_radius_narrows_the_match_limit():
    c = unit(rng.normal(size=128))
    probe = unit(c + 0.3 * unit(rng.normal(size=128)))
    dist = float(np.linalg.norm(probe - c))
    assert cluster.match_face(probe, [ref(c, radius=0.7)], SPEC) is not None
    assert cluster.match_face(probe, [ref(c, radius=0.7, collision=dist - 0.05)], SPEC) is None


def test_hamming_pairs_and_signed_hashes():
    hashes = {1: 0b1111, 2: 0b1110, 3: 0xFFFFFFFFFFFFFFFF, 4: 0}
    pairs = neardup.hamming_pairs(hashes, 1)
    assert (1, 2, 1) in pairs and all(d <= 1 for _, _, d in pairs)
    assert neardup.to_unsigned(neardup.to_signed(0xFFFFFFFFFFFFFFFF)) == 0xFFFFFFFFFFFFFFFF
    assert neardup.to_signed(5) == 5
    assert neardup.hamming_pairs({1: 1}, 5) == []


def test_group_pairs_merges_transitively():
    groups = neardup.group_pairs([(1, 2, 0), (2, 3, 0), (7, 8, 0)])
    assert groups == [[1, 2, 3], [7, 8]]
