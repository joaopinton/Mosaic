from pathlib import Path

import pytest

from mosaic.ai import labels as labels_mod
from mosaic.meta import sources
from mosaic.organize import tags

VOCABULARY = Path(__file__).resolve().parent.parent / "models" / "labels_it.yaml"
BODY_LABELS = {"Persona", "Persone", "Uomo", "Donna", "Bambino", "Neonato", "Ritratto", "Selfie", "Sorriso", "Mani"}
SITUATION_LABELS = {"Folla", "Coppia", "Famiglia", "Amici", "Sposi", "Pilota", "Musicista"}


def test_vocabulary_has_no_labels_describing_the_person_itself():
    names = {v.name for v in labels_mod.load_vocabulary(VOCABULARY)}
    assert not names & BODY_LABELS
    assert SITUATION_LABELS <= names


def make_photo(ctx, uid: str) -> int:
    return ctx.db.conn().execute(
        "INSERT INTO photos(uid, created_at, updated_at) VALUES (?, 'x', 'x')", (uid,)).lastrowid


def label(ctx, pid: int, name: str, src: int) -> None:
    conn = ctx.db.conn()
    tags.set_photo_label(conn, pid, tags.get_or_create_label(conn, name, "Persone"), 10, src)


def names_of(ctx, pid: int) -> set[str]:
    return {r["name"] for r in tags.photo_labels(ctx.db.conn(), pid)}


def test_stale_automatic_labels_are_removed_but_manual_ones_stay(ctx):
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Persona", sources.ONNX)
    label(ctx, pid, "Folla", sources.ONNX)
    label(ctx, pid, "Uomo", sources.MANUAL)
    removed = labels_mod.prune_stale_labels(ctx.db.conn(), {"Folla"})
    assert removed == 1
    assert names_of(ctx, pid) == {"Folla", "Uomo"}


def test_labels_left_without_photos_are_deleted(ctx):
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Persona", sources.ONNX)
    labels_mod.prune_stale_labels(ctx.db.conn(), {"Moto"})
    assert ctx.db.scalar("SELECT COUNT(*) FROM labels WHERE name='Persona'") == 0


def test_prune_is_a_noop_when_everything_is_in_the_vocabulary(ctx):
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Folla", sources.ONNX)
    assert labels_mod.prune_stale_labels(ctx.db.conn(), {"Folla"}) == 0
    assert names_of(ctx, pid) == {"Folla"}


def test_prune_ignores_case_and_accents(ctx):
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Città di notte", sources.ONNX)
    assert labels_mod.prune_stale_labels(ctx.db.conn(), {"città di notte"}) == 0


def test_prune_from_context_reads_the_vocabulary_file(ctx, tmp_path):
    vocab = tmp_path / "labels_it.yaml"
    vocab.write_text("labels:\n  - {it: moto, en: a motorcycle, cat: Moto}\n", encoding="utf-8")
    ctx.models_dir = tmp_path
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Persona", sources.ONNX)
    label(ctx, pid, "Moto", sources.ONNX)
    assert labels_mod.prune_for_context(ctx) == 1
    assert names_of(ctx, pid) == {"Moto"}


def test_prune_from_context_without_vocabulary_touches_nothing(ctx, tmp_path):
    ctx.models_dir = tmp_path / "vuoto"
    pid = make_photo(ctx, "a")
    label(ctx, pid, "Persona", sources.ONNX)
    assert labels_mod.prune_for_context(ctx) == 0
    assert names_of(ctx, pid) == {"Persona"}


@pytest.mark.parametrize("name", sorted(BODY_LABELS))
def test_each_removed_label_is_really_gone_from_the_yaml(name):
    text = VOCABULARY.read_text(encoding="utf-8").lower()
    assert f"it: {name.lower()}," not in text
