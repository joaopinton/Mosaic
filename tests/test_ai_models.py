import hashlib
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg, needs_faces, needs_siglip
from PIL import Image

from mosaic.ai.faces import FaceEngine, overlap, pil_to_bgr
from mosaic.ai.labels import LabelClassifier, LabelDef, load_vocabulary
from mosaic.ai.nsfw import SAFE_PROMPTS, UNSAFE_PROMPTS, Nsfw, NsfwDetector
from mosaic.ai.registry import FaceSpec, RecognizerSpec, embedding_spec, face_spec, llm_spec
from mosaic.ai.siglip import Siglip, l2_normalize

MODELS = Path(__file__).resolve().parent.parent / "models"
DIM = 64


def text_vec(text: str) -> np.ndarray:
    seed = int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).normal(size=DIM)
    return (v / np.linalg.norm(v)).astype(np.float32)


class FakeSiglip:
    model_id = "fake"
    calls = 0

    def embed_texts(self, texts):
        type(self).calls += 1
        return np.stack([text_vec(t) for t in texts])


def vocab(n=50):
    return [LabelDef(f"Etichetta{i}", "Test", (f"prompt {i}a", f"prompt {i}b")) for i in range(n)]


def test_real_vocabulary_is_valid():
    labels = load_vocabulary(MODELS / "labels_it.yaml")
    assert len(labels) >= 250
    assert len({item.name.lower() for item in labels}) == len(labels)
    assert all(item.category and len(item.prompts) == 2 and item.prompts[0].startswith("una foto di") for item in labels)
    assert {"Moto", "Auto", "Persone", "Paesaggi", "Eventi"} <= {item.category for item in labels}
    assert any(item.name == "Ducati" for item in labels)


def test_vocabulary_rejects_empty_file(tmp_path):
    empty = tmp_path / "v.yaml"
    empty.write_text("labels: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_vocabulary(empty)


def test_classifier_picks_the_matching_label_and_respects_limits(tmp_path):
    clf = LabelClassifier(FakeSiglip(), vocab(), cache_dir=tmp_path)
    target = clf.matrix[3]
    noise = np.random.default_rng(1).normal(0, 0.05, size=DIM)
    image = l2_normalize((target + noise)[None, :])
    (preds,) = clf.classify(image, threshold=0.2, max_labels=5)
    assert preds[0].label.name == "Etichetta3" and preds[0].confidence > 0.9
    assert len(preds) <= 5
    mixed = l2_normalize((clf.matrix[3] + clf.matrix[10])[None, :])
    (two,) = clf.classify(mixed, threshold=0.2, max_labels=1)
    assert len(two) == 1
    random_img = l2_normalize(np.random.default_rng(5).normal(size=(1, DIM)))
    assert clf.classify(random_img, threshold=0.2)[0] == []


def test_label_embeddings_are_cached_by_vocabulary_content(tmp_path):
    FakeSiglip.calls = 0
    first = LabelClassifier(FakeSiglip(), vocab(), cache_dir=tmp_path)
    _ = first.matrix
    assert FakeSiglip.calls == 1 and list(tmp_path.glob("labels_fake_*.npy"))
    second = LabelClassifier(FakeSiglip(), vocab(), cache_dir=tmp_path)
    assert np.allclose(second.matrix, first.matrix) and FakeSiglip.calls == 1
    changed = LabelClassifier(FakeSiglip(), vocab(51), cache_dir=tmp_path)
    _ = changed.matrix
    assert FakeSiglip.calls == 2


def test_per_label_threshold_overrides_global():
    labels = [LabelDef("A", "x", ("a",), threshold=0.999), LabelDef("B", "x", ("b",))]
    clf = LabelClassifier(FakeSiglip(), labels)
    img = clf.matrix[0][None, :]
    names = [p.label.name for p in clf.classify(img, 0.2)[0]]
    assert "B" not in names  # l'immagine coincide con A: B è lontana


class NsfwFake:
    model_id = "fake"

    def embed_texts(self, texts):
        out = []
        for t in texts:
            base = np.zeros(DIM, dtype=np.float32)
            if t in UNSAFE_PROMPTS:
                base[0] = 1.0
            else:
                base[1] = 1.0
            base += np.random.default_rng(int(hashlib.sha1(t.encode()).hexdigest()[:6], 16)).normal(0, 0.01, DIM)
            out.append(base / np.linalg.norm(base))
        return np.stack(out)


def test_nsfw_three_valued_result():
    det = NsfwDetector(NsfwFake())
    unsafe_img = np.zeros((1, DIM), dtype=np.float32)
    unsafe_img[0, 0] = 1
    safe_img = np.zeros((1, DIM), dtype=np.float32)
    safe_img[0, 1] = 1
    assert det.classify(unsafe_img) == [Nsfw.UNSAFE]
    assert det.classify(safe_img) == [Nsfw.SAFE]
    assert len(UNSAFE_PROMPTS) and len(SAFE_PROMPTS)


def test_nsfw_is_unavailable_when_model_is_missing(tmp_path):
    from mosaic.ai.runtime import OnnxRuntime
    from mosaic.config import AiConfig

    missing = Siglip(OnnxRuntime(AiConfig()), tmp_path, embedding_spec(MODELS, "siglip2-base-224"))
    assert not missing.available
    assert NsfwDetector(missing).classify(np.zeros((2, 768), dtype=np.float32)) == [Nsfw.UNAVAILABLE] * 2


def test_registry_defaults_and_unknown_models():
    assert embedding_spec(MODELS, "siglip2-base-224").dim == 768
    assert (face_spec(MODELS).recognizer.id, face_spec(MODELS).recognizer.dim) == ("arcface-w600k-r50", 512)
    assert RecognizerSpec().cluster_dist == 0.72 and FaceSpec().cluster_core == 5  # valori predefiniti del codice (SFace)
    assert llm_spec(MODELS, "qwen25vl-3b").model.endswith(".gguf")
    with pytest.raises(KeyError):
        embedding_spec(MODELS, "inesistente")
    with pytest.raises(KeyError):
        llm_spec(MODELS, "inesistente")


def test_overlap_measure():
    a = np.array([0, 0, 10, 10.0])
    assert overlap(a, np.array([0, 0, 10, 10.0])) == 1.0
    assert overlap(a, np.array([20, 20, 5, 5.0])) == 0.0
    assert overlap(a, np.array([5, 0, 10, 10.0])) == 0.5


@needs_siglip
def test_siglip_embeddings_are_normalized_and_color_aware(ctx):
    s = Siglip.for_context(ctx)
    imgs = [Image.new("RGB", (256, 256), c) for c in ((220, 20, 20), (20, 20, 220))]
    vecs = s.embed_images(imgs)
    assert vecs.shape == (2, 768) and np.allclose(np.linalg.norm(vecs, axis=1), 1, atol=1e-4)
    text = s.embed_texts(["a solid red image", "a solid blue image"])
    sims = vecs @ text.T
    assert sims[0, 0] > sims[0, 1] and sims[1, 1] > sims[1, 0]


@needs_siglip
def test_siglip_text_is_case_and_batch_independent(ctx):
    s = Siglip.for_context(ctx)
    a = s.embed_texts(["una MOTO rossa"])[0]
    b = s.embed_texts(["una moto rossa", "tutt'altro"])[0]
    assert float(a @ b) > 0.999
    assert s.embed_images([]).shape == (0, 768) and s.embed_texts([]).shape == (0, 768)


@needs_siglip
def test_italian_and_english_queries_agree(ctx):
    s = Siglip.for_context(ctx)
    img = s.embed_images([Image.new("RGB", (224, 224), (230, 20, 20))])
    it, en = s.embed_texts(["un'immagine tutta rossa"])[0], s.embed_texts(["an entirely red image"])[0]
    blue = s.embed_texts(["un'immagine tutta blu"])[0]
    assert float(img[0] @ it) > float(img[0] @ blue) and float(it @ en) > 0.5


@needs_siglip
def test_analyze_end_to_end_on_real_model(ctx, library):
    from mosaic.ai.analyze import analyze_photos, pending_photo_ids
    from mosaic.ai.vectors import get_index
    from mosaic.index.indexer import index_roots
    from mosaic.pipeline import process_photo

    make_jpeg(library / "red.jpg", color=(220, 20, 20), size=(900, 700))
    make_jpeg(library / "blue.jpg", color=(20, 20, 220), size=(900, 700))
    make_jpeg(library / "green.jpg", color=(20, 200, 20), size=(900, 700))
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    assert pending_photo_ids(ctx) == sorted(ids)
    stats = analyze_photos(ctx, pending_photo_ids(ctx))
    assert stats.photos == 3 and stats.failed == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM embeddings") == 3
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos WHERE analyzed_at IS NOT NULL AND phash IS NOT NULL") == 3
    assert ctx.db.scalar("SELECT MAX(c) FROM (SELECT COUNT(*) c FROM photos_labels GROUP BY photo_id)") <= 5
    assert pending_photo_ids(ctx) == []
    assert len(pending_photo_ids(ctx, force=True)) == 3
    index = get_index(ctx)
    found, scores = index.search(index.vector(ids[0]), 3)
    assert found[0] == ids[0] and scores[0] > 0.999
    analyze_photos(ctx, ids)  # rianalisi: nessun duplicato
    assert ctx.db.scalar("SELECT COUNT(*) FROM embeddings") == 3


@needs_faces
def test_face_engine_loads_and_finds_nothing_in_noise(tmp_path):
    engine = FaceEngine(MODELS)
    assert engine.available
    noise = Image.fromarray(np.random.default_rng(3).integers(0, 255, (480, 640, 3), dtype=np.uint8))
    assert engine.detect(noise, 640) == []


@needs_faces
def test_face_embedding_is_normalized_128d():
    engine = FaceEngine(MODELS, FaceSpec())   # SFace
    img = Image.fromarray(np.random.default_rng(4).integers(0, 255, (400, 400, 3), dtype=np.uint8))
    row = np.array([100, 100, 120, 120, 130, 140, 190, 140, 160, 175, 135, 200, 185, 200, 0.99], dtype=np.float32)
    box = engine._box(row, pil_to_bgr(img), 1.0, 400, 400, 400)
    assert box.embedding.shape == (128,) and abs(float(np.linalg.norm(box.embedding)) - 1) < 1e-3
    assert box.size == 120 and box.score == 99 and len(box.landmarks) == 5


def test_face_engine_reports_missing_models(tmp_path):
    from mosaic.ai.siglip import ModelMissing

    engine = FaceEngine(tmp_path)
    assert not engine.available
    with pytest.raises(ModelMissing):
        engine.detect(Image.new("RGB", (64, 64)), 64)
