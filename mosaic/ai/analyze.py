"""Analisi AI di un lotto di foto: embedding SigLIP, etichette, NSFW, pHash e volti."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass

import imagehash
import numpy as np
from PIL import Image

from mosaic.ai import neardup
from mosaic.ai.faces import FaceBox, FaceEngine, overlap
from mosaic.ai.labels import LabelClassifier, load_vocabulary
from mosaic.ai.nsfw import Nsfw, NsfwDetector
from mosaic.ai.registry import face_spec
from mosaic.ai.siglip import ModelMissing, Siglip, runtime_for
from mosaic.ai.vectors import save_embeddings
from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.store import new_uid, primary_file
from mosaic.meta import sources
from mosaic.organize import tags
from mosaic.organize.people import update_counts
from mosaic.pipeline import thumb_source
from mosaic.search import fts

log = logging.getLogger(__name__)

VOCABULARY_FILE = "labels_it.yaml"
_lock = threading.Lock()


@dataclass
class AnalyzeStats:
    photos: int = 0
    failed: int = 0
    labels: int = 0
    faces: int = 0


def classifier_for(ctx: Context, siglip: Siglip) -> LabelClassifier:
    def build() -> LabelClassifier:
        vocab = load_vocabulary(ctx.models_dir / VOCABULARY_FILE)
        return LabelClassifier(siglip, vocab, cache_dir=ctx.data_dir / "cache")
    return ctx.service("labels", build)


def face_engine_for(ctx: Context) -> FaceEngine:
    return ctx.service("faces", lambda: FaceEngine(ctx.models_dir, face_spec(ctx.models_dir), runtime_for(ctx)))


def release_models(ctx: Context) -> None:
    """Libera la RAM dei modelli (prima di avviare l'LLM)."""
    runtime = ctx._extras.get("onnx")
    if runtime is not None:
        runtime.unload()
    ctx._extras.pop("faces", None)


def pending_photo_ids(ctx: Context, force: bool = False, limit: int | None = None) -> list[int]:
    """Foto elaborate (meta_at) ma non ancora analizzate con il modello corrente."""
    sql = "SELECT p.id FROM photos p WHERE p.deleted_at IS NULL AND p.meta_at IS NOT NULL"
    params: tuple = ()
    if not force:
        sql += " AND (p.analyzed_at IS NULL OR NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.photo_id=p.id AND e.model_id=?))"
        params = (ctx.cfg.ai.model,)
    sql += " ORDER BY p.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["id"] for r in ctx.db.all(sql, params)]


def _phash(img: Image.Image) -> int:
    return neardup.to_signed(int(str(imagehash.phash(img)), 16))


def _store_faces(ctx: Context, photo_id: int, file_id: int, boxes: list[FaceBox], detector_id: str, recognizer_id: str) -> None:
    with ctx.db.tx() as conn:
        # i volti già nominati restano; quelli automatici senza nome si rifanno da zero
        conn.execute("DELETE FROM markers WHERE photo_id=? AND src=? AND name='' AND subj_id IS NULL",
                     (photo_id, sources.ONNX))
        kept = conn.execute("SELECT x, y, w, h FROM markers WHERE photo_id=?", (photo_id,)).fetchall()
        for box in boxes:
            region = np.array([box.x, box.y, box.w, box.h])
            if any(overlap(region, np.array([k["x"], k["y"], k["w"], k["h"]])) > 0.42 for k in kept):
                continue
            conn.execute(
                "INSERT INTO markers(uid, file_id, photo_id, src, embedding, embed_model, detect_model, landmarks, "
                "x, y, w, h, size, score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_uid("m"), file_id, photo_id, sources.ONNX,
                 None if box.embedding is None else box.embedding.astype(np.float32).tobytes(), recognizer_id,
                 detector_id, json.dumps(box.landmarks), box.x, box.y, box.w, box.h, box.size, box.score))


def _detect_faces(ctx: Context, engine: FaceEngine, photo_id: int, image: Image.Image) -> list[FaceBox]:
    main = primary_file(ctx.db, photo_id)
    if main is None:
        return []
    big_path = thumb_source(ctx, photo_id, "fit_1920")
    original_width = max(main["width"], image.width)
    if big_path is None:
        return engine.detect(image, original_width)
    with Image.open(big_path) as big:
        big.load()
        return engine.detect(image, original_width, big if big.width > image.width else None)


EMBED_CHUNK = 2  # immagini per chiamata a SigLIP: tra una e l'altra si controlla se l'utente ha annullato


def _cancelled(control) -> bool:  # type: ignore[no-untyped-def]
    return control is not None and control.cancel.is_set()


def _embed(siglip: Siglip, images: list[Image.Image], control) -> np.ndarray | None:  # type: ignore[no-untyped-def]
    """Embedding a piccoli blocchi; None se l'utente annulla nel frattempo (il lotto si rifà la prossima volta)."""
    parts = []
    for start in range(0, len(images), EMBED_CHUNK):
        if _cancelled(control):
            return None
        parts.append(siglip.embed_images(images[start:start + EMBED_CHUNK]))
    return np.concatenate(parts) if parts else np.zeros((0, 1), dtype=np.float32)


def analyze_photos(ctx: Context, photo_ids: Sequence[int], control=None) -> AnalyzeStats:  # type: ignore[no-untyped-def]
    """Analizza le foto indicate a lotti. Serializzato: i modelli ONNX non vanno eseguiti in parallelo su 8 GB di RAM."""
    cfg = ctx.cfg.ai
    stats = AnalyzeStats()
    with _lock:
        siglip = Siglip.for_context(ctx)
        if not siglip.available:
            raise ModelMissing("SigLIP 2 non scaricato: esegui `python -m mosaic.assets fetch --group core`")
        classifier = classifier_for(ctx, siglip)
        nsfw = NsfwDetector(siglip) if cfg.detect_nsfw else None
        faces = face_engine_for(ctx) if cfg.faces else None
        use_faces = faces is not None and faces.available
        total = len(photo_ids)
        for start in range(0, total, cfg.batch_size):
            if control is not None and control.cancel.is_set():
                break
            batch = list(photo_ids[start:start + cfg.batch_size])
            loaded: list[tuple[int, Image.Image]] = []
            for pid in batch:
                path = thumb_source(ctx, pid, "fit_720")
                if path is None:
                    stats.failed += 1
                    ctx.db.log_error("analyze", str(pid), "miniatura non disponibile")
                    continue
                try:
                    with Image.open(path) as img:
                        img.load()
                        loaded.append((pid, img.convert("RGB")))
                except Exception as exc:
                    stats.failed += 1
                    ctx.db.log_error("analyze", str(pid), f"immagine illeggibile: {exc}")
            if not loaded:
                continue
            ids = [pid for pid, _ in loaded]
            vectors = _embed(siglip, [img for _, img in loaded], control)
            if vectors is None:
                break
            predictions = classifier.classify(vectors, cfg.label_threshold, cfg.max_labels)
            verdicts = nsfw.classify(vectors) if nsfw else [Nsfw.UNAVAILABLE] * len(ids)
            face_boxes: dict[int, list[FaceBox]] = {}
            if use_faces:
                for pid, img in loaded:
                    if _cancelled(control):
                        break
                    try:
                        face_boxes[pid] = _detect_faces(ctx, faces, pid, img)  # type: ignore[arg-type]
                    except Exception as exc:
                        ctx.db.log_error("faces", str(pid), f"{type(exc).__name__}: {exc}")
            if _cancelled(control):  # lotto a metà: non si salva niente, queste foto si rianalizzano la prossima volta
                break
            with ctx.db.tx() as conn:
                save_embeddings(conn, siglip.model_id, ids, vectors)
                for (pid, img), preds, verdict in zip(loaded, predictions, verdicts, strict=True):
                    tags.clear_labels(conn, pid, sources.ONNX)
                    for pred in preds:
                        lid = tags.get_or_create_label(conn, pred.label.name, pred.label.category, pred.label.priority)
                        tags.set_photo_label(conn, pid, lid, round((1 - pred.confidence) * 100), sources.ONNX)
                        stats.labels += 1
                    conn.execute("UPDATE photos SET phash=?, analyzed_at=?, updated_at=? WHERE id=?",
                                 (_phash(img), utcnow(), utcnow(), pid))
                    if verdict is Nsfw.UNSAFE:
                        conn.execute("UPDATE photos SET nsfw=1, private=1 WHERE id=?", (pid,))
                    elif verdict is Nsfw.SAFE:
                        conn.execute("UPDATE photos SET nsfw=0 WHERE id=?", (pid,))
                    fts.reindex(conn, pid)
            for pid, boxes in face_boxes.items():
                main = primary_file(ctx.db, pid)
                if main is not None:
                    _store_faces(ctx, pid, main["id"], boxes, faces.spec.detector.id, faces.spec.recognizer.id)  # type: ignore[union-attr]
                    ctx.db.conn().execute("UPDATE photos SET faces_at=? WHERE id=?", (utcnow(), pid))
                    stats.faces += len(boxes)
            stats.photos += len(loaded)
            if control is not None:
                control.progress(min(1.0, (start + len(batch)) / total), f"{stats.photos}/{total} foto analizzate",
                                 start + len(batch), total)
        if use_faces:
            with ctx.db.tx() as conn:
                update_counts(conn)
    return stats
