"""Clustering dei volti: DBSCAN euclideo su vettori L2-normalizzati, centroide, raggio e regole di match.

Strategia ripresa da PhotoPrism (internal/photoprism/faces_cluster.go, faces_match.go, pkg/vector/alg):
 - i gruppi troppo larghi si ri-clusterizzano con distanza di legame ×0,95, al massimo 6 round, poi si scartano;
 - il raggio di un cluster è il 95° percentile delle distanze dei membri, limitato a cluster_radius;
 - un volto entra in un cluster se dist <= min(raggio, cluster_radius) + cluster_slack;
 - se i due cluster più vicini hanno persone diverse, il più vicino deve battere l'altro di match_margin.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from mosaic.ai.registry import RecognizerSpec

SHRINK = 0.95
MAX_ROUNDS = 6
RADIUS_PERCENTILE = 95
EPSILON = 0.001
COLLISION_DIST = 0.05


@dataclass(frozen=True)
class Cluster:
    members: tuple[int, ...]       # indici nelle righe passate in input
    centroid: np.ndarray
    radius: float


def dbscan(vectors: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN: ritorna le etichette di cluster per riga, -1 = rumore."""
    n = len(vectors)
    labels = np.full(n, -1, dtype=np.int64)
    if n == 0 or n < min_samples:
        return labels
    tree = cKDTree(vectors)
    neighbours = tree.query_ball_point(vectors, r=eps)
    core = np.array([len(nb) >= min_samples for nb in neighbours])
    cluster = 0
    for start in range(n):
        if labels[start] != -1 or not core[start]:
            continue
        labels[start] = cluster
        stack = [start]
        while stack:
            current = stack.pop()
            if not core[current]:
                continue
            for other in neighbours[current]:
                if labels[other] == -1:
                    labels[other] = cluster
                    stack.append(other)
        cluster += 1
    return labels


def make_centroid(vectors: np.ndarray) -> np.ndarray:
    mean = vectors.mean(axis=0)
    return (mean / max(float(np.linalg.norm(mean)), 1e-12)).astype(np.float32)


def distances(vectors: np.ndarray, point: np.ndarray) -> np.ndarray:
    return np.linalg.norm(vectors - point, axis=1)


def cluster_radius(vectors: np.ndarray, centroid: np.ndarray, cap: float) -> float:
    return float(min(np.percentile(distances(vectors, centroid), RADIUS_PERCENTILE), cap))


def face_id(centroid: np.ndarray) -> str:
    """Identificativo stabile di un cluster: hash del centroide."""
    return hashlib.sha1(np.asarray(centroid, dtype=np.float32).tobytes(), usedforsecurity=False).hexdigest()[:32]


def cluster_faces(vectors: np.ndarray, spec: RecognizerSpec, core: int) -> list[Cluster]:
    """Raggruppa i volti. I gruppi più larghi di cluster_radius + cluster_slack vengono ri-clusterizzati più stretti."""
    result: list[Cluster] = []
    limit = spec.cluster_radius + spec.cluster_slack

    def split(indices: np.ndarray, eps: float, rounds: int) -> None:
        labels = dbscan(vectors[indices], eps, core)
        for lab in sorted(set(labels.tolist()) - {-1}):
            members = indices[labels == lab]
            centroid = make_centroid(vectors[members])
            width = float(distances(vectors[members], centroid).max())
            if width <= limit:
                result.append(Cluster(tuple(int(m) for m in members), centroid,
                                      cluster_radius(vectors[members], centroid, spec.cluster_radius)))
            elif rounds < MAX_ROUNDS:
                split(members, eps * SHRINK, rounds + 1)
            # oltre i round massimi il gruppo si scarta

    split(np.arange(len(vectors)), spec.cluster_dist, 0)
    return result


@dataclass(frozen=True)
class FaceRef:
    """Cluster esistente, come serve al match."""
    id: str
    centroid: np.ndarray
    sample_radius: float
    collision_radius: float = 0.0
    subj_id: int | None = None


def match_limit(face: FaceRef, spec: RecognizerSpec) -> float:
    limit = min(face.sample_radius, spec.cluster_radius) + spec.cluster_slack
    if face.collision_radius > 0:
        limit = min(limit, face.collision_radius)
    return min(limit, spec.max_match_dist)


def match_face(vec: np.ndarray, faces: list[FaceRef], spec: RecognizerSpec) -> tuple[FaceRef, float] | None:
    """Cluster più vicino che accetta il volto, oppure None se nessuno lo accetta o il match è ambiguo."""
    if not faces:
        return None
    centroids = np.stack([f.centroid for f in faces])
    dist = np.linalg.norm(centroids - vec, axis=1)
    order = np.argsort(dist)
    best = int(order[0])
    if dist[best] > match_limit(faces[best], spec):
        return None
    if len(order) > 1:
        second = int(order[1])
        different = faces[best].subj_id != faces[second].subj_id
        if different and dist[second] <= match_limit(faces[second], spec) and dist[second] - dist[best] < spec.match_margin:
            return None
    return faces[best], float(dist[best])
