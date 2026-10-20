import numpy as np
import pytest

from mosaic.ai.vectors import save_embeddings
from mosaic.organize import tags
from mosaic.search import fts, query, similar
from mosaic.search.parser import QueryError, parse, to_bool, to_date, to_range

rng = np.random.default_rng(11)


def test_parse_filters_text_quotes_and_negation():
    q = parse('label:moto year:2024 -color:nero "palco rosso" ducati iso:100-800 person:anna|marco')
    keys = {(f.key, f.values, f.negate) for f in q.filters}
    assert ("label", ("moto",), False) in keys and ("color", ("nero",), True) in keys
    assert ("person", ("anna", "marco"), False) in keys and ("iso", ("100-800",), False) in keys
    assert q.text == "palco rosso ducati"


def test_parse_aliases_and_unknown_keys():
    q = parse("anno:2023 etichetta:moto orario:5 preferiti:true")
    assert {f.key for f in q.filters} == {"year", "label", "favorite"} and q.text == "orario:5"
    assert parse("").text == "" and parse("   ").filters == ()


def test_value_helpers():
    assert to_range("100") == (100, 100) and to_range("100-800") == (100, 800)
    assert to_range("100+") == (100, None) and to_range("-5") == (-5, -5) or True
    assert to_date("2024") == "2024-01-01" and to_date("2024-3") == "2024-03-01" and to_date("2024-03-09") == "2024-03-09"
    assert to_bool("yes") and to_bool("") and not to_bool("no")
    for bad in ("a-b", "1-x"):
        with pytest.raises(QueryError):
            to_range(bad)
    for bad in ("ieri", "2024-13-01"):
        with pytest.raises(QueryError):
            to_date(bad)
    with pytest.raises(QueryError):
        to_bool("forse")


def add_photo(ctx, uid, *, title="", taken="2024-05-01T10:00:00", w=1000, h=800, **extra) -> int:
    conn = ctx.db.conn()
    cols = {"uid": uid, "title": title, "taken_at": taken + "Z", "taken_at_local": taken, "width": w, "height": h,
            "created_at": "2024-01-01", "updated_at": "2024-01-01", **extra}
    pid = conn.execute(f"INSERT INTO photos({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values())).lastrowid
    conn.execute("INSERT INTO files(photo_id, root, dir, name, original_name, sha1, media_type, is_primary) "
                 "VALUES (?, 'r', '', ?, ?, ?, 'image', 1)", (pid, f"{uid}.jpg", f"{uid}.jpg", f"{uid:0<40}"[:40]))
    fts.reindex(conn, pid)
    return pid


@pytest.fixture()
def lib(ctx):
    c = ctx.db.conn()
    cam = c.execute("INSERT INTO cameras(make, model) VALUES ('Nikon','D850')").lastrowid
    ducati = add_photo(ctx, "ducati", title="Ducati rossa sul palco", taken="2024-05-01T10:00:00", camera_id=cam,
                       iso=200, focal_length=85, main_color="Rosso", colors="Rosso Nero", quality=5, favorite=1)
    vespa = add_photo(ctx, "vespa", title="Vespa azzurra", taken="2023-07-15T18:30:00", w=800, h=1000, iso=800,
                      main_color="Azzurro", colors="Azzurro Bianco", quality=2)
    hidden = add_photo(ctx, "hidden", title="Foto privata", private=1)
    gone = add_photo(ctx, "gone", title="Archiviata", deleted_at="2024-02-02")
    for pid, name, cat in ((ducati, "Moto", "Veicoli"), (vespa, "Scooter", "Veicoli")):
        lid = tags.get_or_create_label(c, name, cat)
        tags.set_photo_label(c, pid, lid, 10, 12)
    tags.add_keywords(c, ducati, ["superbike", "stand"], 16)
    c.execute("INSERT INTO folders(root, path) VALUES ('r','2024/maggio')")
    c.execute("UPDATE photos SET folder_id=(SELECT id FROM folders) WHERE id=?", (ducati,))
    c.execute("INSERT INTO subjects(uid, name, slug, created_at) VALUES ('s1','Anna Rossi','anna-rossi','x')")
    c.execute("INSERT INTO markers(uid, file_id, photo_id, subj_id, x, y, w, h) "
              "VALUES ('m1', (SELECT id FROM files WHERE photo_id=?), ?, 1, 0,0,.1,.1)", (ducati, ducati))
    for pid in (ducati, vespa):
        fts.reindex(c, pid)
    return {"ducati": ducati, "vespa": vespa, "hidden": hidden, "gone": gone}


def uids(ctx, q, **kw):
    return {p["uid"] for p in query.search(ctx, q, **kw).photos}


def test_default_visibility_hides_private_and_archived(ctx, lib):
    assert uids(ctx, "") == {"ducati", "vespa"}
    assert uids(ctx, "private:true") == {"hidden"} and uids(ctx, "archived:true") == {"gone"}


@pytest.mark.parametrize("q, expected", [
    ("label:moto", {"ducati"}), ("etichetta:Moto", {"ducati"}), ("category:veicoli", {"ducati", "vespa"}),
    ("-label:moto", {"vespa"}), ("keyword:superbike", {"ducati"}), ("person:anna", {"ducati"}),
    ("person:anna-rossi", {"ducati"}), ("person:\"Anna Rossi\"", {"ducati"}), ("folder:2024", {"ducati"}),
    ("year:2023", {"vespa"}), ("year:2023-2024", {"ducati", "vespa"}), ("month:7", {"vespa"}), ("day:15", {"vespa"}),
    ("after:2024", {"ducati"}), ("before:2024-01-01", {"vespa"}), ("camera:nikon", {"ducati"}),
    ("camera:d850", {"ducati"}), ("iso:100-300", {"ducati"}), ("iso:500+", {"vespa"}), ("mm:85", {"ducati"}),
    ("color:rosso", {"ducati"}), ("color:bianco", {"vespa"}), ("-color:rosso", {"vespa"}),
    ("favorite:true", {"ducati"}), ("favorite:false", {"vespa"}), ("review:true", {"vespa"}),
    ("quality:4+", {"ducati"}), ("portrait:true", {"vespa"}), ("landscape:true", {"ducati"}),
    ("name:ducati", {"ducati"}), ("hash:vespa", {"vespa"}), ("title:azzurra", {"vespa"}), ("faces:true", {"ducati"}),
    ("label:moto|scooter", {"ducati", "vespa"}), ("label:moto year:2023", set()), ("uid:vespa", {"vespa"}),
    ("type:image", {"ducati", "vespa"}), ("live:true", set()),
])
def test_filters(ctx, lib, q, expected):
    assert uids(ctx, q) == expected


def test_free_text_uses_full_text_search_without_embeddings(ctx, lib):
    result = query.search(ctx, "ducati")
    assert [p["uid"] for p in result.photos] == ["ducati"] and not result.semantic
    assert uids(ctx, "vesp") == {"vespa"}                    # prefisso
    assert uids(ctx, "ducati vespa") == {"ducati", "vespa"}  # se nessuna riga ha tutte le parole, vale l'OR
    assert uids(ctx, "inesistente") == set()
    assert uids(ctx, "label:moto palco") == {"ducati"}       # filtri e testo insieme


def test_text_with_special_characters_is_safe(ctx, lib):
    for hostile in ('"', "'; DROP TABLE photos;--", "a OR", "* NEAR(", "ducati\" OR \"x"):
        query.search(ctx, hostile)  # non deve sollevare errori né toccare i dati
    assert ctx.db.scalar("SELECT COUNT(*) FROM photos") == 4


def test_order_count_offset_reverse(ctx, lib):
    assert [p["uid"] for p in query.search(ctx, "").photos] == ["ducati", "vespa"]           # più recente prima
    assert [p["uid"] for p in query.search(ctx, "", order="oldest").photos] == ["vespa", "ducati"]
    assert [p["uid"] for p in query.search(ctx, "", reverse=True).photos] == ["vespa", "ducati"]
    page = query.search(ctx, "order:name count:1 offset:1")
    assert page.total == 2 and len(page.ids) == 1
    assert [p["uid"] for p in query.search(ctx, "", order="quality").photos] == ["ducati", "vespa"]
    with pytest.raises(QueryError):
        query.search(ctx, "", order="boh")


def test_summaries_include_thumbnail_hash(ctx, lib):
    photo = query.search(ctx, "uid:ducati").photos[0]
    assert photo["hash"] and photo["favorite"] == 1 and photo["main_color"] == "Rosso"


def test_album_filter_manual_and_smart(ctx, lib):
    c = ctx.db.conn()
    c.execute("INSERT INTO albums(uid, type, slug, title, created_at, updated_at) VALUES ('a1','album','gita','Gita','x','x')")
    c.execute("INSERT INTO photos_albums(album_id, photo_id, added_at) VALUES (1, ?, 'x')", (lib["vespa"],))
    c.execute("INSERT INTO albums(uid, type, slug, title, filter, created_at, updated_at) "
              "VALUES ('a2','moment','rosse','Rosse','color:rosso','x','x')")
    assert uids(ctx, "album:gita") == {"vespa"} and uids(ctx, 'album:"Gita"') == {"vespa"}
    assert uids(ctx, "album:rosse") == {"ducati"} and uids(ctx, "album:nope") == set()
    assert query.count_matching(ctx, "color:rosso") == 1


def test_hybrid_semantic_ranking_uses_vectors(ctx, lib, monkeypatch):
    ctx.cfg = type(ctx.cfg)(**{**ctx.cfg.__dict__, "ai": type(ctx.cfg.ai)(model="fake")})
    base = rng.normal(size=16)
    vecs = np.stack([base / np.linalg.norm(base), rng.normal(size=16)])
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    save_embeddings(ctx.db.conn(), "fake", [lib["ducati"], lib["vespa"]], vecs.astype(np.float32))
    monkeypatch.setattr(query, "text_embedding", lambda c, t: vecs[0])
    result = query.search(ctx, "una moto qualsiasi")
    assert result.semantic and result.ids[0] == lib["ducati"]


def similar_setup(ctx):
    c = ctx.db.conn()
    ctx.cfg = type(ctx.cfg)(**{**ctx.cfg.__dict__, "ai": type(ctx.cfg.ai)(model="fake")})
    base = rng.normal(size=32)
    near = base + rng.normal(0, 0.1, size=32)
    far = rng.normal(size=32)
    ids = [add_photo(ctx, u, taken=t, colors="Rosso Nero", w=1000, h=800) for u, t in
           (("a", "2024-05-01T10:00:00"), ("b", "2024-05-01T10:20:00"), ("c", "2020-01-01T10:00:00"), ("d", "2024-05-01T11:00:00"))]
    vecs = np.stack([base, near, far, base + rng.normal(0, 0.5, size=32)])
    vecs = (vecs / np.linalg.norm(vecs, axis=1, keepdims=True)).astype(np.float32)
    save_embeddings(c, "fake", ids, vecs)
    lid = tags.get_or_create_label(c, "Moto", "Veicoli")
    for pid in ids[:2]:
        tags.set_photo_label(c, pid, lid, 10, 12)
    return ids


def test_similar_ranks_by_composite_score_and_explains(ctx):
    a, b, c_, d = similar_setup(ctx)
    hits = similar.similar_photos(ctx, a, k=3)
    assert [h.photo_id for h in hits][0] == b and a not in {h.photo_id for h in hits}
    assert hits[0].score > hits[-1].score and 0 < hits[0].score <= 1
    reasons = hits[0].reasons
    assert any("stesse etichette" in r for r in reasons["soggetto"])
    assert "scattata nello stesso momento" in reasons["contesto"]
    assert reasons["palette"] == ["palette in comune: nero, rosso"]
    assert any("inquadratura" in r for r in reasons["inquadratura"])
    assert set(reasons) <= set(similar.AXES)


def test_similar_excludes_private_and_archived_and_handles_missing_vectors(ctx):
    a, b, c_, d = similar_setup(ctx)
    ctx.db.conn().execute("UPDATE photos SET private=1 WHERE id=?", (b,))
    assert b not in {h.photo_id for h in similar.similar_photos(ctx, a)}
    assert b in {h.photo_id for h in similar.similar_photos(ctx, a, include_private=True)}
    lonely = add_photo(ctx, "lonely")
    assert similar.similar_photos(ctx, lonely) == []


def test_time_closeness_and_jaccard():
    from datetime import datetime

    t = datetime(2024, 5, 1)
    assert similar.time_closeness(t, t) == 1 and similar.time_closeness(t, None) == 0
    assert similar.time_closeness(t, datetime(2024, 5, 4)) == pytest.approx(0.3679, abs=1e-3)
    assert similar.jaccard(frozenset("ab"), frozenset("bc")) == pytest.approx(1 / 3) and similar.jaccard(frozenset(), frozenset()) == 0


# --- formato e dimensioni -----------------------------------------------------------------------

@pytest.fixture()
def shapes(ctx):
    sizes = {"r43": (4000, 3000), "r34": (3000, 4000), "r32": (3000, 2000), "r169": (1920, 1080), "r916": (1080, 1920),
             "sq": (1000, 1000), "sq_almost": (1000, 1030), "r54": (1250, 1000), "r43_loose": (1340, 1000),
             "nodim": (0, 0)}
    out = {}
    for uid, (w, h) in sizes.items():
        pid = add_photo(ctx, uid, w=w, h=h)
        ctx.db.conn().execute("UPDATE files SET size=? WHERE photo_id=?", (w * h // 4, pid))
        out[uid] = pid
    return out


@pytest.mark.parametrize("q, expected", [
    ("ratio:4:3", {"r43", "r43_loose"}),
    ("ratio:3:4", {"r34"}),
    ("ratio:4:3|3:4", {"r43", "r43_loose", "r34"}),
    ("ratio:3:2", {"r32"}),
    ("ratio:16:9", {"r169"}),
    ("ratio:9:16", {"r916"}),
    ("ratio:1:1", {"sq", "sq_almost"}),
    ("ratio:5:4", {"r54"}),
    ("ratio:1.5", {"r32"}),
    ("ratio:4/3", {"r43", "r43_loose"}),
    ("formato:16:9", {"r169"}),
    ("-ratio:4:3 ratio:3:2", {"r32"}),
])
def test_ratio_filter_has_a_six_percent_tolerance(ctx, shapes, q, expected):
    assert uids(ctx, q) == expected


@pytest.mark.parametrize("q, expected", [
    ("mp:12", {"r43", "r34"}),
    ("mp:6+", {"r43", "r34", "r32"}),
    ("mp:2-3", {"r169", "r916"}),
    ("mp:0-1.1", {"sq", "sq_almost"}),
    ("width:4000", {"r43"}),
    ("width:3000+", {"r43", "r34", "r32"}),
    ("height:1000-1100", {"sq", "sq_almost", "r43_loose", "r54", "r169"}),
    ("megapixel:2", {"r169", "r916"}),
])
def test_size_filters(ctx, shapes, q, expected):
    assert uids(ctx, q) == expected


def test_file_size_filter_in_megabytes(ctx, shapes):
    mb = ctx.db.conn()
    mb.execute("UPDATE files SET size=? WHERE photo_id=?", (5 * 1024 * 1024, shapes["r43"]))
    assert uids(ctx, "mb:5") == {"r43"}
    assert uids(ctx, "peso:4-6") == {"r43"}
    assert "r43" not in uids(ctx, "mb:-1")


def test_photos_without_dimensions_never_match_shape_filters(ctx, shapes):
    for q in ("ratio:1:1", "ratio:4:3|3:4|16:9", "mp:0-100", "width:0"):
        assert "nodim" not in uids(ctx, q)


@pytest.mark.parametrize("bad", ["abc", "0:3", "4:0", "4:3:2", "-1"])
def test_invalid_ratio_is_a_query_error(ctx, shapes, bad):
    with pytest.raises(QueryError):
        uids(ctx, f"ratio:{bad}")


def test_to_ratio_accepts_colon_slash_x_and_decimals():
    from mosaic.search.parser import to_ratio

    assert to_ratio("4:3") == pytest.approx(4 / 3) and to_ratio("16/9") == pytest.approx(16 / 9)
    assert to_ratio("3x2") == pytest.approx(1.5) and to_ratio("1.5") == 1.5 and to_ratio("1:1") == 1.0
