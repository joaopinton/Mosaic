"""scripts/face_eval.py: metriche TAR/FAR e proposta di soglie sui volti nominati."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from mosaic.meta import sources
from mosaic.organize.people import ensure_subject

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "face_eval.py"
spec = importlib.util.spec_from_file_location("face_eval", SCRIPT)
fe = importlib.util.module_from_spec(spec)
sys.modules["face_eval"] = fe  # i dataclass dello script cercano il proprio modulo
spec.loader.exec_module(fe)


def test_pair_distances_split_same_and_different_people():
    vectors = np.array([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    d = fe.pair_distances(vectors, [1, 1, 2])
    assert d.genuine.tolist() == [0.0]
    assert np.allclose(sorted(d.impostor), [np.sqrt(2)] * 2)


def test_pair_distances_ignore_same_person_pairs_inside_one_photo():
    vectors = np.array([[1, 0], [0.9, 0.1], [0, 1]], dtype=np.float32)
    d = fe.pair_distances(vectors, [1, 1, 2], photos=[7, 7, 8])
    assert len(d.genuine) == 0 and len(d.impostor) == 2


def test_threshold_and_tar_at_far():
    impostor = np.linspace(1.0, 2.0, 1001)
    assert fe.threshold_at_far(impostor, 0.0) < 1.0
    assert fe.threshold_at_far(impostor, 0.01) == pytest.approx(1.01, abs=1e-3)
    assert np.isnan(fe.threshold_at_far(np.array([]), 0.01))
    assert fe.tar_at(np.array([0.5, 0.9, 1.5]), 1.0) == pytest.approx(2 / 3)
    assert np.isnan(fe.tar_at(np.array([]), 1.0))


def test_report_proposes_thresholds_below_the_impostors():
    rng = np.random.default_rng(1)
    d = fe.Distances(genuine=rng.normal(0.8, 0.1, 500), impostor=rng.normal(1.4, 0.05, 2000))
    report = fe.build_report("prova", d)
    assert report.cluster_dist < report.match_dist <= report.max_match_dist < 1.4
    assert report.tar[0.01] > 0.9
    text = fe.format_report(report)
    assert "TAR" in text and "cluster_dist" in text and "prova" in text


def test_stored_vectors_refuse_embeddings_of_another_model():
    rows = [{"id": 1, "embed_model": "x", "embedding": np.zeros(4, np.float32).tobytes()}]
    with pytest.raises(SystemExit):
        fe.stored_vectors(rows, "y")
    assert fe.stored_vectors(rows, "x").shape == (1, 4)


def test_named_markers_only_count_manual_names(ctx):
    conn = ctx.db.conn()
    pid = conn.execute("INSERT INTO photos(uid, created_at, updated_at) VALUES ('p', 'x', 'x')").lastrowid
    fid = conn.execute("INSERT INTO files(photo_id, root, name, original_name, media_type) VALUES (?, 'r', 'a', 'a', 'image')", (pid,)).lastrowid
    anna = ensure_subject(conn, "Anna")
    for uid, src in (("manuale", sources.MANUAL), ("automatico", sources.ONNX)):
        conn.execute("INSERT INTO markers(uid, file_id, photo_id, src, subj_id, landmarks, x, y, w, h) VALUES (?,?,?,?,?,?,0,0,.1,.1)",
                     (uid, fid, pid, src, anna, json.dumps([[0, 0]] * 5)))
    assert len(fe.named_markers(conn)) == 1
