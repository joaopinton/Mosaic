import ast
import os
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import make_jpeg, needs_faces, needs_siglip
from PIL import Image

import mosaic
from mosaic.ai.runtime import OnnxRuntime
from mosaic.config import AiConfig
from mosaic.index.indexer import index_roots
from mosaic.meta.dates import Taken
from mosaic.meta.extract import MetaData
from mosaic.pipeline import process_photo

PACKAGE = Path(mosaic.__file__).parent


def run_pipeline(ctx) -> list[int]:
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    return ids


def test_blas_threads_are_limited_at_import():
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        assert int(os.environ[var]) >= 1
    assert int(os.environ["OPENBLAS_NUM_THREADS"]) <= max(1, (os.cpu_count() or 2))


def test_no_hardcoded_drive_paths_in_the_code():
    """Il vecchio Mosaic aveva `e:\\padova moto days 2026` in ogni script: qui nessun percorso di disco è scritto nel codice."""
    pattern = re.compile(r"^[A-Za-z]:[\\/]")
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
                    and pattern.match(node.value):
                offenders.append(f"{path.relative_to(PACKAGE)}: {node.value!r}")
    assert offenders == []


def test_dml_request_falls_back_to_cpu_when_unavailable():
    assert OnnxRuntime(AiConfig(provider="cpu")).providers == ["CPUExecutionProvider"]
    assert OnnxRuntime(AiConfig(provider="dml")).providers[-1] == "CPUExecutionProvider"


def test_stacking_by_date_gps_and_serial_only_when_enabled(ctx, library, monkeypatch):
    fixed = MetaData(taken=Taken("2024-05-01T10:00:00Z", "2024-05-01T10:00:00", ""), taken_src=16, lat=45.4, lng=11.8, serial="S-1")
    monkeypatch.setattr("mosaic.pipeline.extract.extract", lambda data, name, mtime: fixed)
    make_jpeg(library / "burst1.jpg", color=(1, 1, 1))
    make_jpeg(library / "burst2.jpg", color=(200, 1, 1))
    run_pipeline(ctx)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 2          # per default le raffiche non si uniscono

    ctx.cfg = replace(ctx.cfg, stack_meta=True)
    for pid in [r["id"] for r in ctx.db.all("SELECT id FROM photos")]:
        process_photo(ctx, pid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM files") == 2


def test_heic_is_converted_to_a_browser_preview(ctx, library):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    try:
        Image.new("RGB", (300, 200), (30, 120, 220)).save(library / "foto.heic", quality=80)
    except Exception as exc:  # senza encoder HEIF non si può creare il file di prova
        pytest.skip(f"encoder HEIF assente: {exc}")
    (pid,) = run_pipeline(ctx)
    row = ctx.db.one("SELECT * FROM files WHERE photo_id=?", (pid,))
    assert row["media_type"] == "heic"
    assert (ctx.sidecar_dir / f"{row['sha1']}.jpg").exists()
    assert ctx.db.scalar("SELECT main_color FROM photos WHERE id=?", (pid,)) == "Blu"
    assert ctx.db.scalar("SELECT width FROM photos WHERE id=?", (pid,)) == 300


def test_unreadable_raw_is_reported_and_does_not_stop_the_pipeline(ctx, library):
    (library / "scatto.CR2").write_bytes(b"II*\x00" + b"non-un-vero-raw" * 40)
    make_jpeg(library / "ok.jpg", color=(120, 30, 30))
    ids = run_pipeline(ctx)
    assert len(ids) == 2
    raw = ctx.db.one("SELECT f.*, p.type, p.quality FROM files f JOIN photos p ON p.id=f.photo_id WHERE f.name='scatto.CR2'")
    assert raw["type"] == "raw" and raw["error"] == "conversione fallita"
    assert ctx.db.scalar("SELECT COUNT(*) FROM errors WHERE scope='process'") == 1
    assert ctx.db.scalar("SELECT main_color FROM photos p JOIN files f ON f.photo_id=p.id WHERE f.name='ok.jpg'") != ""


@needs_siglip
def test_text_embeddings_are_the_same_whatever_the_batching(ctx):
    from mosaic.ai.siglip import TEXT_BATCH, Siglip

    siglip = Siglip.for_context(ctx)
    prompts = [f"una foto di oggetto numero {n}" for n in range(TEXT_BATCH * 2 + 6)]
    together = siglip.embed_texts(prompts)
    assert together.shape == (len(prompts), 768)
    for index in (0, TEXT_BATCH - 1, TEXT_BATCH, len(prompts) - 1):
        alone = siglip.embed_texts([prompts[index]])[0]
        assert float(np.dot(together[index], alone)) > 0.999


@needs_faces
def test_face_engine_recovers_after_a_failed_initialisation(monkeypatch):
    import cv2

    from mosaic.ai.faces import FaceEngine
    from mosaic.ai.registry import FaceSpec
    from mosaic.paths import MODELS_DIR

    engine = FaceEngine(MODELS_DIR, FaceSpec())     # riconoscitore SFace (OpenCV)
    real = cv2.FaceRecognizerSF.create
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise cv2.error("bad allocation")
        return real(*args, **kwargs)

    monkeypatch.setattr(cv2.FaceRecognizerSF, "create", staticmethod(flaky))
    with pytest.raises(cv2.error):
        engine._sface()
    assert engine._load() is not None and engine._sface() is not None   # il secondo tentativo ricarica, nessuno stato a metà
