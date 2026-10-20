"""ArcFace: specifica, allineamento a 112 px, embedding a batch e ricalcolo dei vettori esistenti."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from conftest import needs_arcface, needs_faces

from mosaic.ai import face_worker
from mosaic.ai.faces import ARCFACE_REFERENCE, FaceEngine, align_112, frontal
from mosaic.ai.registry import FaceSpec, RecognizerSpec, face_spec
from mosaic.organize.people import ensure_subject

MODELS = Path(__file__).resolve().parent.parent / "models"
ARC_ID = "arcface-w600k-r50"
LANDMARKS = [130, 140, 190, 145, 160, 175, 135, 200, 185, 205]


def arc_spec(flip: bool = False) -> FaceSpec:
    return FaceSpec(recognizer=RecognizerSpec(id=ARC_ID, file="faces/w600k_r50.onnx", engine="arcface", dim=512, flip=flip))


def yunet_row() -> np.ndarray:
    return np.array([100, 100, 120, 120, *LANDMARKS, 0.99], dtype=np.float32)


def texture(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.integers(0, 255, (400, 400, 3), dtype=np.uint8), (0, 0), 3)


def test_registry_defaults_to_sface():
    spec = RecognizerSpec()
    assert (spec.engine, spec.input_size, spec.flip) == ("sface", 112, False)


def test_registry_parses_arcface_entry(tmp_path):
    (tmp_path / "models.yaml").write_text(
        "faces:\n  recognizer:\n    id: x\n    engine: arcface\n    flip: true\n    dim: 512\n", encoding="utf-8")
    spec = face_spec(tmp_path).recognizer
    assert (spec.engine, spec.flip, spec.dim, spec.input_size) == ("arcface", True, 512, 112)


def test_align_112_maps_landmarks_onto_the_reference_template():
    points = np.asarray(LANDMARKS, dtype=np.float32).reshape(5, 2)
    assert align_112(texture(), points).shape == (112, 112, 3)
    # immagine con un segno su ciascun punto: dopo l'allineamento i segni cadono sul modello di riferimento
    marked = np.zeros((400, 400, 3), dtype=np.uint8)
    for x, y in points:
        cv2.circle(marked, (round(float(x)), round(float(y))), 3, (255, 255, 255), -1)
    aligned = align_112(marked, points)[..., 0]
    for ref in ARCFACE_REFERENCE:
        x, y = round(float(ref[0])), round(float(ref[1]))
        assert aligned[max(0, y - 3):y + 4, max(0, x - 3):x + 4].max() > 0


def test_align_112_survives_degenerate_points():
    assert align_112(texture(), np.zeros((5, 2), dtype=np.float32)).shape == (112, 112, 3)


@needs_faces
def test_align_112_matches_opencv_align_crop():
    """Verifica che l'ordine dei punti di YuNet sia quello atteso dal modello di riferimento."""
    img = texture(2)
    recognizer = cv2.FaceRecognizerSF.create(str(MODELS / "faces" / "face_recognition_sface_2021dec.onnx"), "")
    reference = recognizer.alignCrop(img, yunet_row().reshape(1, 15))
    ours = align_112(img, yunet_row()[4:14])
    assert np.abs(reference.astype(int) - ours.astype(int)).mean() < 1.0
    swapped = align_112(img, yunet_row()[4:14].reshape(5, 2)[[1, 0, 2, 4, 3]])
    assert np.abs(reference.astype(int) - swapped.astype(int)).mean() > 3.0


@needs_arcface
def test_arcface_embeddings_are_512d_normalized_and_batched():
    engine = FaceEngine(MODELS, arc_spec())
    assert engine.available
    rows = np.stack([yunet_row()] * 3)
    rows[1, 4:14] += 20
    vectors = engine.embed_rows(texture(), rows)
    assert vectors.shape == (3, 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4)
    assert np.allclose(vectors[0], vectors[2], atol=1e-5) and not np.allclose(vectors[0], vectors[1], atol=1e-3)


@needs_arcface
def test_arcface_flip_changes_the_vector_but_keeps_it_normalized():
    plain = FaceEngine(MODELS, arc_spec(False)).embed_rows(texture(), yunet_row().reshape(1, 15))
    flipped = FaceEngine(MODELS, arc_spec(True)).embed_rows(texture(), yunet_row().reshape(1, 15))
    assert abs(float(np.linalg.norm(flipped)) - 1) < 1e-4
    assert not np.allclose(plain, flipped, atol=1e-4) and float(plain[0] @ flipped[0]) > 0.5


@needs_arcface
def test_boxes_are_built_through_the_arcface_engine():
    engine = FaceEngine(MODELS, arc_spec())
    box = engine._box(yunet_row(), texture(), 1.0, 400, 400, 400)
    assert box.embedding.shape == (512,) and box.size == 120 and box.score == 99 and len(box.landmarks) == 5


def test_embed_rows_with_no_rows_is_empty():
    assert FaceEngine(MODELS, arc_spec()).embed_rows(texture(), np.zeros((0, 15), dtype=np.float32)).shape == (0, 512)


def test_arcface_engine_reports_missing_model(tmp_path):
    from mosaic.ai.siglip import ModelMissing

    engine = FaceEngine(tmp_path, arc_spec())
    assert not engine.available
    with pytest.raises(ModelMissing):
        engine.embed_rows(texture(), yunet_row().reshape(1, 15))


# --- frontal ----------------------------------------------------------------------------

FRONT = [(0.40, 0.40), (0.60, 0.40), (0.50, 0.50), (0.42, 0.60), (0.58, 0.60)]


def test_frontal_accepts_straight_faces_and_rejects_profiles():
    assert frontal(FRONT)
    turned = [(0.40, 0.40), (0.60, 0.40), (0.66, 0.50), (0.45, 0.60), (0.62, 0.60)]  # naso fuori dall'asse degli occhi
    assert not frontal(turned)
    assert not frontal([(0.5, 0.4), (0.5, 0.4), (0.5, 0.5), (0.4, 0.6), (0.6, 0.6)])  # occhi sovrapposti: profilo
    assert frontal([])  # senza punti non si esclude


# --- ricalcolo dei vettori --------------------------------------------------------------

class FakeEngine:
    """Sostituto di FaceEngine: un embedding fisso per riga, senza modelli."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed_rows(self, bgr, rows):
        self.calls.append(len(rows))
        out = np.zeros((len(rows), 512), dtype=np.float32)
        out[:, 0] = 1.0
        return out


def add_old_marker(ctx, photo_id, name="", model="sface-2021dec"):
    conn = ctx.db.conn()
    file_id = conn.execute("SELECT id FROM files WHERE photo_id=?", (photo_id,)).fetchone()["id"]
    subj = ensure_subject(conn, name) if name else None
    return conn.execute(
        "INSERT INTO markers(uid, file_id, photo_id, src, name, subj_id, embedding, embed_model, landmarks, x, y, w, h, size, score) "
        "VALUES (?,?,?,12,?,?,?,?,?,0.25,0.25,0.3,0.3,120,95)",
        (f"m{photo_id}-{name}-{model}", file_id, photo_id, name, subj, np.ones(128, np.float32).tobytes(), model,
         json.dumps(FRONT))).lastrowid


@pytest.fixture()
def old_library(ctx, monkeypatch):
    conn = ctx.db.conn()
    photos = []
    for i in range(2):
        pid = conn.execute("INSERT INTO photos(uid, created_at, updated_at) VALUES (?, 'x', 'x')", (f"p{i}",)).lastrowid
        conn.execute("INSERT INTO files(photo_id, root, name, original_name, media_type) VALUES (?, 'r', ?, 'n', 'image')",
                     (pid, f"f{i}.jpg"))
        photos.append(pid)
    monkeypatch.setattr(face_worker, "_marker_image", lambda c, pid: np.zeros((200, 300, 3), dtype=np.uint8))
    return photos


def test_reembed_keeps_subject_and_name_and_drops_old_clusters(ctx, old_library):
    ctx.db.conn().execute(
        "INSERT INTO faces(id, embed_model, embedding, created_at) VALUES ('old', 'sface-2021dec', x'00', 'x')")
    anna = add_old_marker(ctx, old_library[0], "Anna")
    anon = add_old_marker(ctx, old_library[1])
    ctx.db.conn().execute("UPDATE markers SET face_id='old', face_dist=0.4")
    active = face_spec(ctx.models_dir).recognizer.id
    engine = FakeEngine()
    assert face_worker.pending_reembed(ctx) == 2
    stats = face_worker.reembed_markers(ctx, engine=engine)
    assert (stats.done, stats.skipped) == (2, 0) and engine.calls == [1, 1]
    row = ctx.db.one("SELECT * FROM markers WHERE id=?", (anna,))
    assert row["embed_model"] == active and row["name"] == "Anna" and row["subj_id"] is not None
    assert row["face_id"] is None and row["face_dist"] == -1 and len(row["embedding"]) == 512 * 4
    assert ctx.db.one("SELECT * FROM markers WHERE id=?", (anon,))["embed_model"] == active
    assert ctx.db.scalar("SELECT COUNT(*) FROM faces WHERE id='old'") == 0
    assert face_worker.pending_reembed(ctx) == 0


def test_reembed_groups_markers_of_a_photo_in_one_batch(ctx, old_library):
    add_old_marker(ctx, old_library[0], "Anna")
    add_old_marker(ctx, old_library[0], "Marco")
    engine = FakeEngine()
    face_worker.reembed_markers(ctx, engine=engine)
    assert engine.calls == [2]


def test_reembed_skips_unreadable_photos_and_reports_progress(ctx, old_library, monkeypatch):
    add_old_marker(ctx, old_library[0], "Anna")
    add_old_marker(ctx, old_library[1], "Marco")
    monkeypatch.setattr(face_worker, "_marker_image",
                        lambda c, pid: None if pid == old_library[1] else np.zeros((50, 50, 3), np.uint8))
    seen: list[str] = []
    stats = face_worker.reembed_markers(ctx, engine=FakeEngine(), progress=lambda v, m, *_counts: seen.append(m))
    assert (stats.done, stats.skipped) == (1, 1) and seen


def test_reembed_stops_when_cancelled(ctx, old_library):
    add_old_marker(ctx, old_library[0], "Anna")
    add_old_marker(ctx, old_library[1], "Marco")
    stats = face_worker.reembed_markers(ctx, engine=FakeEngine(), is_cancelled=lambda: True)
    assert (stats.done, stats.skipped) == (0, 2)


def test_reembed_does_nothing_when_all_markers_are_current(ctx):
    assert face_worker.reembed_markers(ctx, engine=FakeEngine()) == face_worker.ReembedStats()


def test_yunet_rows_scale_normalized_boxes_to_pixels():
    marker = {"x": 0.25, "y": 0.5, "w": 0.1, "h": 0.2, "score": 90, "landmarks": json.dumps(FRONT)}
    rows = face_worker._yunet_rows([marker], 200, 100)
    assert rows[0, :4].tolist() == [50.0, 50.0, 20.0, 20.0]
    assert rows[0, 4] == pytest.approx(80.0) and rows[0, 5] == pytest.approx(40.0) and rows[0, 14] == pytest.approx(0.9)


# --- h_faces ----------------------------------------------------------------------------

class FakeControl:
    def __init__(self) -> None:
        import threading

        self.cancel = threading.Event()
        self.messages: list[str] = []
        self.submitted: list[str] = []

    def progress(self, value, message="", *_counts):
        self.messages.append(message)

    def submit(self, job_type, payload=None, unique=False):
        self.submitted.append(job_type)
        return 1


def test_h_faces_recalculates_vectors_after_a_model_change_then_groups(ctx, old_library, monkeypatch):
    from mosaic.jobs.handlers import h_faces

    add_old_marker(ctx, old_library[0], "Anna")
    seen = {}
    monkeypatch.setattr(face_worker, "reembed_markers", lambda c, **kw: seen.setdefault("done", True))
    ctl = FakeControl()
    h_faces(ctx, None, ctl)
    assert seen == {"done": True} and ctl.submitted == ["moments"] and "volti riconosciuti" in ctl.messages[-1]


def test_h_faces_skips_when_the_model_is_missing(ctx, old_library, monkeypatch):
    from mosaic.ai.siglip import ModelMissing
    from mosaic.jobs.handlers import h_faces

    add_old_marker(ctx, old_library[0], "Anna")

    def missing(c, **kw):
        raise ModelMissing("manca arcface")

    monkeypatch.setattr(face_worker, "reembed_markers", missing)
    ctl = FakeControl()
    h_faces(ctx, None, ctl)
    assert ctl.submitted == [] and "modello mancante" in ctl.messages[-1]
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='faces'") == 1


def test_h_faces_stops_when_cancelled_during_the_recalculation(ctx, old_library, monkeypatch):
    from mosaic.jobs.handlers import h_faces

    add_old_marker(ctx, old_library[0], "Anna")
    ctl = FakeControl()
    monkeypatch.setattr(face_worker, "reembed_markers", lambda c, **kw: ctl.cancel.set())
    h_faces(ctx, None, ctl)
    assert ctl.submitted == []


def test_h_faces_without_stale_markers_goes_straight_to_grouping(ctx, monkeypatch):
    from mosaic.jobs.handlers import h_faces

    monkeypatch.setattr(face_worker, "reembed_markers", lambda c, **kw: pytest.fail("non serve ricalcolare"))
    ctl = FakeControl()
    h_faces(ctx, None, ctl)
    assert ctl.submitted == ["moments"]
