"""Taratura del riconoscimento dei volti sui marker con un nome (la verità di riferimento è quella data dall'utente).

Calcola le distanze tra tutte le coppie di volti nominati: coppie della stessa persona (genuine) e di persone
diverse (impostori). Da queste ricava il tasso di riconoscimento (TAR) a falsi positivi (FAR) fissati e propone
le soglie di models.yaml.

Uso:
  python scripts/face_eval.py                    # vettori già salvati nel DB (modello attivo)
  python scripts/face_eval.py --compare          # ricalcola SFace e ArcFace sugli stessi ritagli e li confronta
  python scripts/face_eval.py --compare --flip   # ArcFace anche con la media del ritaglio specchiato
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANUAL = 64  # mosaic.meta.sources.MANUAL
FAR_LEVELS = (0.001, 0.01)  # 0,1% e 1%
MATCH_FAR = 0.005           # soglia di auto-assegnazione e di suggerimento: FAR ≤ 0,5%
SUGGEST_FAR = 0.01


@dataclass(frozen=True)
class Distances:
    """Distanze euclidee tra coppie di volti nominati."""
    genuine: np.ndarray      # stessa persona
    impostor: np.ndarray     # persone diverse


@dataclass(frozen=True)
class Report:
    label: str
    genuine_count: int
    impostor_count: int
    genuine_median: float
    genuine_p95: float
    impostor_p1: float
    tar: dict[float, float]          # FAR → TAR
    threshold: dict[float, float]    # FAR → soglia di distanza
    cluster_dist: float
    match_dist: float
    max_match_dist: float


def pair_distances(vectors: np.ndarray, subjects: Sequence[int], photos: Sequence[int] | None = None) -> Distances:
    """Distanze per coppia. Se due volti sono nella stessa foto non sono mai la stessa persona e si ignorano le coppie genuine."""
    vectors = np.asarray(vectors, dtype=np.float32)
    subj = np.asarray(subjects)
    n = len(vectors)
    dist = np.linalg.norm(vectors[:, None, :] - vectors[None, :, :], axis=2)
    upper = np.triu(np.ones((n, n), dtype=bool), k=1)
    same = subj[:, None] == subj[None, :]
    if photos is not None:
        ph = np.asarray(photos)
        same_photo = ph[:, None] == ph[None, :]
        upper &= ~(same & same_photo)
    return Distances(dist[upper & same], dist[upper & ~same])


def threshold_at_far(impostor: np.ndarray, far: float) -> float:
    """Distanza massima accettata perché non più di `far` delle coppie di persone diverse la superi in basso."""
    if len(impostor) == 0:
        return float("nan")
    if far <= 0:
        return float(np.min(impostor)) - 1e-4
    return float(np.quantile(impostor, far))


def tar_at(genuine: np.ndarray, threshold: float) -> float:
    return float(np.mean(genuine <= threshold)) if len(genuine) else float("nan")


def build_report(label: str, d: Distances) -> Report:
    thresholds = {far: threshold_at_far(d.impostor, far) for far in FAR_LEVELS}
    return Report(
        label=label, genuine_count=len(d.genuine), impostor_count=len(d.impostor),
        genuine_median=float(np.median(d.genuine)) if len(d.genuine) else float("nan"),
        genuine_p95=float(np.percentile(d.genuine, 95)) if len(d.genuine) else float("nan"),
        impostor_p1=float(np.percentile(d.impostor, 1)) if len(d.impostor) else float("nan"),
        tar={far: tar_at(d.genuine, t) for far, t in thresholds.items()},
        threshold=thresholds,
        cluster_dist=threshold_at_far(d.impostor, 0.0),
        match_dist=threshold_at_far(d.impostor, MATCH_FAR),
        max_match_dist=threshold_at_far(d.impostor, SUGGEST_FAR),
    )


def format_report(r: Report) -> str:
    lines = [
        f"== {r.label} ==",
        f"coppie: {r.genuine_count} della stessa persona, {r.impostor_count} di persone diverse",
        f"stessa persona: mediana {r.genuine_median:.3f}, 95° percentile {r.genuine_p95:.3f}",
        f"persone diverse: 1° percentile {r.impostor_p1:.3f}",
    ]
    for far in FAR_LEVELS:
        lines.append(f"TAR {r.tar[far] * 100:5.1f}% a FAR {far * 100:.1f}% (soglia {r.threshold[far]:.3f})")
    lines += [
        "soglie proposte per models.yaml:",
        f"  cluster_dist: {r.cluster_dist:.2f}   (FAR ≈ 0)",
        f"  match_dist: {r.match_dist:.2f}   (FAR ≤ {MATCH_FAR * 100:.1f}%)",
        f"  max_match_dist: {r.max_match_dist:.2f}   (FAR ≤ {SUGGEST_FAR * 100:.1f}%)",
    ]
    return "\n".join(lines)


# --- accesso ai dati -----------------------------------------------------------------------

def named_markers(conn) -> list:  # type: ignore[no-untyped-def]
    """Solo i nomi dati o confermati dall'utente: quelli automatici renderebbero la misura circolare."""
    return conn.execute(
        "SELECT id, photo_id, subj_id, x, y, w, h, score, landmarks, embedding, embed_model FROM markers "
        "WHERE invalid=0 AND subj_id IS NOT NULL AND src>=? AND landmarks!='' ORDER BY photo_id, id", (MANUAL,)).fetchall()


def stored_vectors(rows: Sequence, model_id: str) -> np.ndarray:
    stale = [r["id"] for r in rows if r["embed_model"] != model_id or r["embedding"] is None]
    if stale:
        raise SystemExit(f"{len(stale)} marker non hanno un vettore del modello {model_id}: esegui `python -m mosaic faces`")
    return np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])


def recompute_vectors(ctx, rows: Sequence, spec) -> np.ndarray:  # type: ignore[no-untyped-def]
    """Ricalcola i vettori dei marker con `spec` (stessi ritagli, solo il riconoscitore cambia)."""
    from mosaic.ai.face_worker import _marker_image, _yunet_rows
    from mosaic.ai.faces import FaceEngine

    engine = FaceEngine(ctx.models_dir, spec)
    vectors = np.zeros((len(rows), spec.recognizer.dim), dtype=np.float32)
    by_photo: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        by_photo.setdefault(row["photo_id"], []).append(i)
    for photo_id, idx in by_photo.items():
        image = _marker_image(ctx, photo_id)
        if image is None:
            raise SystemExit(f"immagine della foto {photo_id} non disponibile")
        batch = _yunet_rows([rows[i] for i in idx], image.shape[1], image.shape[0])
        vectors[idx] = engine.embed_rows(image, batch)
    return vectors


def sface_spec(base):  # type: ignore[no-untyped-def]
    from mosaic.ai.registry import RecognizerSpec

    return replace(base, recognizer=RecognizerSpec())


def arcface_spec(base, flip: bool):  # type: ignore[no-untyped-def]
    from mosaic.ai.registry import RecognizerSpec

    active = base.recognizer
    arc = active if active.engine == "arcface" else RecognizerSpec(
        id="arcface-w600k-r50", file="faces/w600k_r50.onnx", engine="arcface", dim=512)
    return replace(base, recognizer=replace(arc, flip=flip))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compare", action="store_true", help="ricalcola SFace e ArcFace sugli stessi ritagli")
    parser.add_argument("--flip", action="store_true", help="ArcFace con la media del ritaglio specchiato")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # la console di Windows usa cp1252

    from mosaic.ai.registry import face_spec
    from mosaic.context import Context

    ctx = Context.open()
    try:
        rows = named_markers(ctx.db.conn())
        subjects = [r["subj_id"] for r in rows]
        photos = [r["photo_id"] for r in rows]
        if len(set(subjects)) < 2:
            print("servono almeno due persone con un nome")
            return 1
        base = face_spec(ctx.models_dir)
        print(f"{len(rows)} volti con un nome, {len(set(subjects))} persone\n")
        if args.compare:
            variants = [("SFace", sface_spec(base)), ("ArcFace" + (" + flip" if args.flip else ""), arcface_spec(base, args.flip))]
            reports = []
            for label, spec in variants:
                vectors = recompute_vectors(ctx, rows, spec)
                reports.append(build_report(label, pair_distances(vectors, subjects, photos)))
                print(format_report(reports[-1]) + "\n")
            better = reports[1].tar[FAR_LEVELS[1]] > reports[0].tar[FAR_LEVELS[1]]
            print(f"ArcFace {'batte' if better else 'NON batte'} SFace: TAR {reports[1].tar[FAR_LEVELS[1]] * 100:.1f}% "
                  f"contro {reports[0].tar[FAR_LEVELS[1]] * 100:.1f}% a FAR 1%")
        else:
            vectors = stored_vectors(rows, base.recognizer.id)
            print(format_report(build_report(base.recognizer.id, pair_distances(vectors, subjects, photos))))
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
