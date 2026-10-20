"""Riconoscimento dei volti dalle persone con un nome: cluster più piccoli, volti frontali, k-NN, una persona per foto."""

import json

import numpy as np
import pytest
from test_face_worker import add_marker, marker, noisy, person

from mosaic.ai import face_worker
from mosaic.ai.registry import face_spec
from mosaic.meta import sources
from mosaic.organize.people import ensure_subject

FRONT = [(0.40, 0.40), (0.60, 0.40), (0.50, 0.50), (0.42, 0.60), (0.58, 0.60)]
PROFILE = [(0.40, 0.40), (0.60, 0.40), (0.72, 0.50), (0.45, 0.60), (0.66, 0.60)]


def rec(ctx):
    return face_spec(ctx.models_dir).recognizer


def with_landmarks(ctx, mid, landmarks):
    ctx.db.conn().execute("UPDATE markers SET landmarks=? WHERE id=?", (json.dumps(landmarks), mid))


def small(ctx, vec, **kwargs):
    """Volto sotto cluster_size (112 px): si riconosce ma non fonda cluster, così si prova solo il passo delle persone."""
    return add_marker(ctx, vec, size=kwargs.pop("size", 100), **kwargs)


def named_marker(ctx, vec, name, **kwargs):
    """Volto nominato dall'utente (src manuale), come dopo il nome dato nella pagina Persone."""
    mid = small(ctx, vec, name=name, **kwargs)
    ctx.db.conn().execute("UPDATE markers SET src=?, subj_id=? WHERE id=?",
                          (sources.MANUAL, ensure_subject(ctx.db.conn(), name), mid))
    return mid


def at_distance(base, distance, seed=97):
    """Vettore unitario a distanza euclidea `distance` da `base`."""
    other = person(seed)
    perp = other - base * float(other @ base)
    perp = perp / np.linalg.norm(perp)
    cos = 1 - distance ** 2 / 2
    return (base * cos + perp * np.sqrt(1 - cos ** 2)).astype(np.float32)


def same_photo(ctx, mid):
    row = ctx.db.one("SELECT photo_id, file_id FROM markers WHERE id=?", (mid,))
    return {"photo_id": row["photo_id"], "file_id": row["file_id"]}


# --- cluster più piccoli e volti frontali ---------------------------------------------------

def test_three_faces_are_enough_for_a_cluster(ctx):
    ids = [add_marker(ctx, v) for v in noisy(person(20), 3)]
    assert face_worker.run_faces(ctx).clusters == 1
    assert len({marker(ctx, i)["face_id"] for i in ids}) == 1


def test_two_faces_form_a_cluster_only_in_the_retry_round(ctx):
    ids = [add_marker(ctx, v) for v in noisy(person(21), 2)]
    stats = face_worker.run_faces(ctx)
    assert stats.clusters == 0 and stats.retry_clusters == 1
    assert marker(ctx, ids[0])["face_id"] is not None


def test_profile_faces_do_not_found_clusters_but_are_recognized_afterwards(ctx):
    base = person(22)
    profiles = [add_marker(ctx, v) for v in noisy(base, 6)]
    for mid in profiles:
        with_landmarks(ctx, mid, PROFILE)
    assert face_worker.run_faces(ctx).clusters == 0
    frontals = [add_marker(ctx, v) for v in noisy(base, 4)]
    for mid in frontals:
        with_landmarks(ctx, mid, FRONT)
    assert face_worker.run_faces(ctx).clusters == 1
    face = marker(ctx, frontals[0])["face_id"]
    assert face is not None and all(marker(ctx, mid)["face_id"] == face for mid in profiles)


# --- persone con un nome ----------------------------------------------------------------------

def test_single_named_face_names_a_lookalike_without_a_cluster(ctx):
    base = person(23)
    named_marker(ctx, base, "Elettra")
    target = small(ctx, noisy(base, 1, 0.01)[0])
    stats = face_worker.run_faces(ctx)
    row = marker(ctx, target)
    assert stats.subjects == 1 and row["name"] == "Elettra" and row["subj_id"] is not None
    assert row["src"] == sources.ONNX                      # il nome automatico non diventa manuale
    assert ctx.db.one("SELECT * FROM subjects WHERE name='Elettra'")["marker_count"] == 2


def test_automatic_names_are_never_used_as_references(ctx):
    base = person(24)
    named_marker(ctx, base, "Elettra")
    first = small(ctx, at_distance(base, rec(ctx).match_dist - 0.05))
    face_worker.run_faces(ctx)
    assert marker(ctx, first)["name"] == "Elettra"
    # vicino a `first` ma oltre il limite dal volto nominato: senza deriva non prende il nome
    drifted = small(ctx, at_distance(base, rec(ctx).max_match_dist + 0.1, seed=96))
    face_worker.run_faces(ctx)
    assert marker(ctx, drifted)["name"] == "" and marker(ctx, drifted)["sugg_subj_id"] is None


def test_intermediate_distance_creates_a_suggestion_not_a_name(ctx):
    base = person(25)
    named_marker(ctx, base, "Ettore")
    spec = rec(ctx)
    wanted = (spec.match_dist + spec.max_match_dist) / 2
    target = small(ctx, at_distance(base, wanted))
    stats = face_worker.run_faces(ctx)
    row = marker(ctx, target)
    assert stats.suggested == 1 and stats.subjects == 0
    assert row["name"] == "" and row["subj_id"] is None
    assert row["sugg_subj_id"] == ctx.db.scalar("SELECT id FROM subjects WHERE name='Ettore'")
    assert row["sugg_dist"] == pytest.approx(wanted, abs=1e-3)


def test_too_far_faces_get_neither_name_nor_suggestion(ctx):
    base = person(26)
    named_marker(ctx, base, "Ettore")
    target = small(ctx, at_distance(base, rec(ctx).max_match_dist + 0.1))
    assert face_worker.run_faces(ctx).suggested == 0
    assert marker(ctx, target)["sugg_subj_id"] is None


def test_ambiguous_match_between_two_people_becomes_a_suggestion(ctx):
    base = person(27)
    named_marker(ctx, base, "Anna")
    named_marker(ctx, noisy(base, 1, 0.0005)[0], "Bea")      # due persone quasi identiche
    target = small(ctx, noisy(base, 1, 0.0003)[0])
    stats = face_worker.run_faces(ctx)
    assert stats.subjects == 0 and stats.suggested == 1 and marker(ctx, target)["name"] == ""


def test_rejected_suggestion_is_not_proposed_again(ctx):
    base = person(28)
    named_marker(ctx, base, "Ettore")
    target = small(ctx, at_distance(base, (rec(ctx).match_dist + rec(ctx).max_match_dist) / 2))
    subj = ctx.db.scalar("SELECT id FROM subjects WHERE name='Ettore'")
    ctx.db.conn().execute("UPDATE markers SET sugg_rejected=? WHERE id=?", (str(subj), target))
    assert face_worker.run_faces(ctx).suggested == 0
    assert marker(ctx, target)["sugg_subj_id"] is None


def test_stale_suggestions_are_recomputed_from_scratch(ctx):
    base = person(33)
    named_marker(ctx, base, "Ettore")
    target = small(ctx, at_distance(base, (rec(ctx).match_dist + rec(ctx).max_match_dist) / 2))
    face_worker.run_faces(ctx)
    ctx.db.conn().execute("UPDATE markers SET embed_model='altro' WHERE id=?", (target,))
    face_worker.run_faces(ctx)
    assert marker(ctx, target)["sugg_subj_id"] is None


def test_subject_already_in_the_photo_is_not_proposed_for_another_face(ctx):
    base = person(29)
    first = named_marker(ctx, base, "Anna")
    twin = small(ctx, noisy(base, 1, 0.01)[0], **same_photo(ctx, first))
    face_worker.run_faces(ctx)
    assert marker(ctx, twin)["name"] == "" and marker(ctx, twin)["sugg_subj_id"] is None


# --- una persona per foto -------------------------------------------------------------------------

def two_automatic_annas(ctx, base):
    anna = named_marker(ctx, base, "Anna")
    subj = ctx.db.scalar("SELECT subj_id FROM markers WHERE id=?", (anna,))
    far = small(ctx, at_distance(base, 0.9))
    near = small(ctx, at_distance(base, 0.3), **same_photo(ctx, far))
    conn = ctx.db.conn()
    conn.execute("UPDATE markers SET subj_id=?, name='Anna', sugg_dist=0.9 WHERE id=?", (subj, far))
    conn.execute("UPDATE markers SET subj_id=?, name='Anna', sugg_dist=0.3 WHERE id=?", (subj, near))
    return far, near


def test_unique_per_photo_keeps_the_closest_automatic_name(ctx):
    far, near = two_automatic_annas(ctx, person(30))
    stats = face_worker.run_faces(ctx)
    assert stats.deduplicated == 1
    assert marker(ctx, near)["name"] == "Anna"
    assert marker(ctx, far)["name"] == "" and marker(ctx, far)["subj_id"] is None and marker(ctx, far)["face_id"] is None


def test_unique_per_photo_never_touches_manual_names(ctx):
    base = person(31)
    first = named_marker(ctx, base, "Anna")
    second = named_marker(ctx, noisy(base, 1, 0.01)[0], "Anna", **same_photo(ctx, first))
    auto = small(ctx, noisy(base, 1, 0.02)[0], **same_photo(ctx, first))
    ctx.db.conn().execute("UPDATE markers SET subj_id=?, name='Anna' WHERE id=?",
                          (ctx.db.scalar("SELECT subj_id FROM markers WHERE id=?", (first,)), auto))
    stats = face_worker.run_faces(ctx)
    assert stats.deduplicated == 1
    assert marker(ctx, first)["name"] == marker(ctx, second)["name"] == "Anna" and marker(ctx, auto)["name"] == ""


def test_subject_step_without_named_faces_does_nothing(ctx):
    small(ctx, person(32))
    stats = face_worker.run_faces(ctx)
    assert (stats.subjects, stats.suggested, stats.deduplicated) == (0, 0, 0)

