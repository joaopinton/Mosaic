"""Contesto per le didascalie: chi sei, la giornata raccontata nell'album, quando è stata scattata la foto."""

from dataclasses import replace

import pytest
from conftest import make_jpeg
from test_llm import fake_command

from mosaic.index.indexer import index_roots
from mosaic.llm import captions, context
from mosaic.llm.server import LlamaServer
from mosaic.llm.worker import caption_photos
from mosaic.organize import albums, moments
from mosaic.pipeline import process_photo


@pytest.mark.parametrize("taken, expected", [
    ("2025-06-14T20:30:00", "il 14 giugno 2025, di sera"),
    ("2025-01-02T07:05:00", "il 2 gennaio 2025, di mattina"),
    ("2025-08-15T13:00:00", "il 15 agosto 2025, nel primo pomeriggio"),
    ("2025-08-15T17:00:00", "il 15 agosto 2025, nel tardo pomeriggio"),
    ("2025-12-31T23:50:00", "il 31 dicembre 2025, di notte"),
    ("2025-12-31T02:10:00", "il 31 dicembre 2025, di notte"),
    ("2025-06-14", "il 14 giugno 2025"),
    (None, ""), ("", ""), ("boh", ""),
])
def test_moment_is_described_in_italian(taken, expected):
    assert context.describe_moment(taken, "it") == expected


def test_moment_in_english_uses_a_plain_date():
    assert context.describe_moment("2025-06-14T20:30:00", "en") == "on 2025-06-14, in the evening"


def test_context_text_joins_only_what_is_known():
    full = context.compose(about="Foto di famiglia.", stories=[("Mare", "Giornata a Jesolo con i nonni.")],
                           when="il 14 giugno 2025, di sera", language="it")
    assert "Foto di famiglia." in full and "«Mare»" in full and "Jesolo" in full and "14 giugno 2025" in full
    assert "non inventare" in full.lower()
    assert context.compose(about="", stories=[], when="", language="it") == ""
    only_date = context.compose(about="", stories=[], when="il 14 giugno 2025", language="it")
    assert "14 giugno 2025" in only_date and "«" not in only_date


def test_long_texts_are_cut_to_keep_the_prompt_small():
    text = context.compose(about="a" * 5000, stories=[("T", "b" * 5000)], when="", language="it")
    assert len(text) < context.MAX_ABOUT + context.MAX_STORY + 400


def test_at_most_two_stories_and_most_specific_first():
    text = context.compose(about="", stories=[("Uno", "u"), ("Due", "d"), ("Tre", "t")], when="", language="it")
    assert "«Uno»" in text and "«Due»" in text and "«Tre»" not in text


def add_photo_in(ctx, library, name, sub=""):
    """Colore diverso per ogni nome: file identici sarebbero la stessa foto per l'indicizzatore."""
    color = (sum(map(ord, name)) % 200 + 20, 60, 90)
    make_jpeg(library / sub / name if sub else library / name, color=color, size=(300, 200))
    return name


def test_stories_follow_album_membership(ctx, library):
    for name, sub in (("a.jpg", ""), ("b.jpg", "gita"), ("c.jpg", "gita")):
        add_photo_in(ctx, library, name, sub)
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    moments.generate(ctx)
    conn = ctx.db.conn()
    root = conn.execute("SELECT * FROM albums WHERE type='folder' AND filter LIKE 'root:%'").fetchone()
    gita = conn.execute("SELECT * FROM albums WHERE type='folder' AND filter LIKE 'folder:%'").fetchone()
    assert root is not None and gita is not None
    albums.update_album(conn, root["id"], description="Compleanno di Stella.")
    albums.update_album(conn, gita["id"], description="Gita al lago.")
    stories = context.load_stories(ctx)
    by_photo = {pid: [t for t, _ in context.stories_for(stories, pid)] for pid in ids}
    sizes = {r["id"]: r["name"] for r in conn.execute("SELECT p.id, f.name FROM photos p JOIN files f ON f.photo_id=p.id")}
    for pid, titles in by_photo.items():
        assert titles[0] == (gita["title"] if sizes[pid] in ("b.jpg", "c.jpg") else root["title"])
        assert root["title"] in titles
    assert len(by_photo[next(p for p in ids if sizes[p] == "a.jpg")]) == 1


def test_albums_without_a_story_are_ignored(ctx, library):
    add_photo_in(ctx, library, "a.jpg")
    for pid in index_roots(ctx).changed_photo_ids:
        process_photo(ctx, pid)
    moments.generate(ctx)
    assert context.load_stories(ctx) == []


def test_caption_prompt_receives_story_about_and_date(ctx, library, monkeypatch):
    add_photo_in(ctx, library, "a.jpg")
    ids = index_roots(ctx).changed_photo_ids
    for pid in ids:
        process_photo(ctx, pid)
    moments.generate(ctx)
    conn = ctx.db.conn()
    root = conn.execute("SELECT id FROM albums WHERE type='folder'").fetchone()
    albums.update_album(conn, root["id"], description="Giornata di prova al lago di Garda.")
    conn.execute("UPDATE photos SET taken_at_local='2025-06-14T20:30:00' WHERE id=?", (ids[0],))
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, about="Siamo una famiglia di Padova."))
    seen = {}
    real = captions.describe

    def spy(base_url, image, language="it", client=None, people=(), context="", **kwargs):
        seen["context"] = context
        return real(base_url, image, language, client, people, context, **kwargs)

    monkeypatch.setattr(captions, "describe", spy)
    server = LlamaServer(ctx, command=fake_command)
    try:
        stats = caption_photos(ctx, ids, server=server)
    finally:
        server.stop()
    assert stats.photos == 1
    assert "Garda" in seen["context"] and "famiglia di Padova" in seen["context"] and "14 giugno 2025" in seen["context"]


def test_context_goes_into_the_user_prompt():
    body = captions.build_payload(b"x", "it", context="CONTESTO-DI-PROVA")
    assert "CONTESTO-DI-PROVA" in body["messages"][1]["content"][-1]["text"]  # dopo l'immagine: cambia da foto a foto
    assert "CONTESTO" not in str(captions.build_payload(b"x", "it")["messages"])
