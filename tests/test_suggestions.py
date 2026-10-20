"""Suggerimenti di persona da confermare: migrazione del DB, azioni su `people` e rotte API."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_api import add_face, lib3, m  # noqa: F401 - fixture condivise con i test dell'API
from test_organize import add_marker, photo

from mosaic.api.app import create_app
from mosaic.db import Database
from mosaic.db import connection as db_connection
from mosaic.meta import sources
from mosaic.organize import people


@pytest.fixture()
def client(m):  # noqa: F811
    return TestClient(create_app(m, allowed_hosts={"testserver"}, start_workers=False))


# --- migrazione ---------------------------------------------------------------------------

def test_migration_adds_suggestion_columns_to_a_version_1_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db_connection.SCHEMA_FILE.read_text(encoding="utf-8"))
    old.execute("INSERT INTO photos(uid, created_at, updated_at) VALUES ('p', 'x', 'x')")
    old.execute("INSERT INTO files(photo_id, root, name, original_name, media_type) VALUES (1, 'r', 'a.jpg', 'a', 'image')")
    old.execute("INSERT INTO subjects(uid, name, slug, created_at) VALUES ('s1', 'Anna', 'anna', 'x')")
    for uid, subj in (("m1", 1), ("m2", None)):
        old.execute("INSERT INTO markers(uid, file_id, photo_id, src, subj_id, x, y, w, h) VALUES (?,1,1,12,?,0,0,.1,.1)", (uid, subj))
    old.execute("PRAGMA user_version=1")
    old.commit()
    old.close()

    db = Database(path)
    assert db.scalar("PRAGMA user_version") >= 2
    cols = {r["name"] for r in db.all("PRAGMA table_info(markers)")}
    assert {"sugg_subj_id", "sugg_dist", "sugg_rejected"} <= cols
    named, anonymous = (db.one("SELECT * FROM markers WHERE uid=?", (u,)) for u in ("m1", "m2"))
    assert named["src"] == sources.MANUAL and anonymous["src"] == sources.ONNX      # i nomi esistenti contano come confermati
    assert anonymous["sugg_subj_id"] is None and anonymous["sugg_dist"] == -1 and anonymous["sugg_rejected"] == ""
    db.close()
    Database(path).close()    # una seconda apertura non rilancia la migrazione


def test_new_database_has_the_latest_version(ctx):
    assert ctx.db.scalar("PRAGMA user_version") == db_connection.MIGRATIONS[-1][0]


# --- people ----------------------------------------------------------------------------------

def suggest(ctx, uid, subject, dist=1.0):
    ctx.db.conn().execute("UPDATE markers SET sugg_subj_id=?, sugg_dist=? WHERE uid=?", (subject, dist, uid))


def setup_people(ctx):
    conn = ctx.db.conn()
    p1, p2 = photo(ctx, "a"), photo(ctx, "b")
    add_marker(ctx, p1, "m1")
    add_marker(ctx, p2, "m2")
    return conn, people.name_marker(conn, "m1", "Anna")


def test_confirm_suggestion_names_the_face_as_manual_and_clears_the_suggestion(ctx):
    conn, anna = setup_people(ctx)
    suggest(ctx, "m2", anna)
    assert people.confirm_suggestion(conn, "m2") == anna
    row = conn.execute("SELECT * FROM markers WHERE uid='m2'").fetchone()
    assert (row["subj_id"], row["name"], row["src"]) == (anna, "Anna", sources.MANUAL)
    assert row["sugg_subj_id"] is None and row["sugg_dist"] == -1
    assert conn.execute("SELECT marker_count FROM subjects WHERE id=?", (anna,)).fetchone()[0] == 2


def test_reject_suggestion_remembers_the_person(ctx):
    conn, anna = setup_people(ctx)
    suggest(ctx, "m2", anna)
    people.reject_suggestion(conn, "m2")
    row = conn.execute("SELECT * FROM markers WHERE uid='m2'").fetchone()
    assert row["sugg_subj_id"] is None and row["subj_id"] is None and row["sugg_rejected"] == str(anna)
    bea = people.name_marker(conn, "m1", "Bea")
    suggest(ctx, "m2", bea)
    people.reject_suggestion(conn, "m2")
    assert conn.execute("SELECT sugg_rejected FROM markers WHERE uid='m2'").fetchone()[0] == f"{anna},{bea}"


def test_confirm_and_reject_need_a_suggestion(ctx):
    conn, _ = setup_people(ctx)
    for fn in (people.confirm_suggestion, people.reject_suggestion):
        with pytest.raises(people.PeopleError, match="nessun suggerimento"):
            fn(conn, "m2")
        with pytest.raises(people.PeopleError, match="inesistente"):
            fn(conn, "zzz")


def test_naming_or_rejecting_a_face_drops_its_suggestion(ctx):
    conn, anna = setup_people(ctx)
    suggest(ctx, "m2", anna)
    people.name_marker(conn, "m2", "Marco")
    assert conn.execute("SELECT sugg_subj_id FROM markers WHERE uid='m2'").fetchone()[0] is None
    add_marker(ctx, photo(ctx, "c"), "m3")
    suggest(ctx, "m3", anna)
    people.reject_marker(conn, "m3")
    assert conn.execute("SELECT sugg_subj_id FROM markers WHERE uid='m3'").fetchone()[0] is None


def test_naming_a_face_makes_it_a_manual_name(ctx):
    conn, _ = setup_people(ctx)
    assert conn.execute("SELECT src FROM markers WHERE uid='m1'").fetchone()[0] == sources.MANUAL


def test_merge_moves_suggestions_to_the_kept_person(ctx):
    conn, anna = setup_people(ctx)
    bea = people.ensure_subject(conn, "Bea")
    suggest(ctx, "m2", bea)
    people.merge_subjects(conn, anna, bea)
    assert conn.execute("SELECT sugg_subj_id FROM markers WHERE uid='m2'").fetchone()[0] == anna


def test_list_suggestions_groups_by_person_closest_first(ctx):
    conn, anna = setup_people(ctx)
    bea = people.ensure_subject(conn, "Bea")
    for uid, subj, dist in (("m3", anna, 1.1), ("m4", anna, 0.9), ("m5", bea, 0.8)):
        add_marker(ctx, photo(ctx, uid), uid)
        suggest(ctx, uid, subj, dist)
    groups = people.list_suggestions(conn)
    assert [g["name"] for g in groups] == ["Bea", "Anna"]
    assert [m["uid"] for m in groups[1]["markers"]] == ["m4", "m3"]
    assert groups[1]["subject"] == conn.execute("SELECT uid FROM subjects WHERE id=?", (anna,)).fetchone()[0]


def test_list_suggestions_skips_named_and_invalid_faces(ctx):
    conn, anna = setup_people(ctx)
    suggest(ctx, "m2", anna)
    conn.execute("UPDATE markers SET invalid=1 WHERE uid='m2'")
    assert people.list_suggestions(conn) == []


# --- API --------------------------------------------------------------------------------------

def api_setup(client, lib3, ctx):  # noqa: F811
    add_face(ctx, lib3[0], "m1")
    add_face(ctx, lib3[1], "m2")
    add_face(ctx, lib3[2], "m3")
    anna = client.post("/api/markers/m1/name", json={"name": "Anna"}).json()["subject"]
    anna_id = ctx.db.scalar("SELECT id FROM subjects WHERE uid=?", (anna,))
    suggest(ctx, "m2", anna_id, 0.95)
    suggest(ctx, "m3", anna_id, 1.05)
    return anna


def test_suggestions_endpoint_lists_groups(client, lib3, ctx):  # noqa: F811
    anna = api_setup(client, lib3, ctx)
    groups = client.get("/api/faces/suggestions").json()
    assert groups == [{"subject": anna, "name": "Anna", "markers": [{"uid": "m2", "dist": 0.95}, {"uid": "m3", "dist": 1.05}]}]


def test_confirm_endpoint(client, lib3, ctx):  # noqa: F811
    anna = api_setup(client, lib3, ctx)
    assert client.post("/api/markers/m2/confirm").json() == {"subject": anna}
    assert ctx.db.one("SELECT name FROM markers WHERE uid='m2'")["name"] == "Anna"
    assert client.post("/api/markers/m2/confirm").status_code == 400      # niente più da confermare
    assert client.post("/api/markers/zzz/confirm").status_code == 400


def test_reject_endpoint(client, lib3, ctx):  # noqa: F811
    api_setup(client, lib3, ctx)
    assert client.post("/api/markers/m2/suggestion/reject").status_code == 204
    assert ctx.db.one("SELECT sugg_subj_id, sugg_rejected FROM markers WHERE uid='m2'")["sugg_subj_id"] is None
    assert client.post("/api/markers/m2/suggestion/reject").status_code == 400


def test_bulk_confirm_endpoint_confirms_valid_uids_and_reports_the_rest(client, lib3, ctx):  # noqa: F811
    api_setup(client, lib3, ctx)
    result = client.post("/api/faces/suggestions/confirm", json={"uids": ["m2", "m3", "zzz"]}).json()
    assert result == {"confirmed": 2, "skipped": ["zzz"]}
    assert {r["name"] for r in ctx.db.all("SELECT name FROM markers WHERE uid IN ('m2','m3')")} == {"Anna"}
    assert client.get("/api/faces/suggestions").json() == []
    assert client.post("/api/faces/suggestions/confirm", json={"uids": []}).status_code == 422
