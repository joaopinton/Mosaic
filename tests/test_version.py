"""Versione unica e aggiornamento del catalogo: copia di sicurezza prima di migrare, rifiuto dei cataloghi più nuovi."""

import re
import sqlite3
from pathlib import Path

import pytest

import mosaic
from mosaic.db import Database
from mosaic.db.connection import LATEST_VERSION, DatabaseTooNewError

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    declared = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert declared and declared.group(1) == mosaic.__version__


def test_old_database_is_backed_up_then_migrated(tmp_path):
    path = tmp_path / "mosaic.db"
    db = Database(path)
    db.conn().execute("PRAGMA user_version=5")  # come un catalogo creato da una versione precedente
    db.conn().execute("DROP INDEX idx_albums_source")
    db.conn().execute("ALTER TABLE albums DROP COLUMN title_manual")
    db.conn().execute("ALTER TABLE albums DROP COLUMN source")
    db.close()
    migrated = Database(path)
    assert migrated.scalar("PRAGMA user_version") == LATEST_VERSION
    backups = list((tmp_path / "backups").glob("mosaic-v5-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 5


def test_up_to_date_and_fresh_databases_make_no_backup(tmp_path):
    Database(tmp_path / "mosaic.db").close()
    Database(tmp_path / "mosaic.db").close()
    assert not (tmp_path / "backups").exists()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "mosaic.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version={LATEST_VERSION + 3}")
    with pytest.raises(DatabaseTooNewError):
        Database(path)


def test_assets_missing_lists_what_an_update_must_download(tmp_path, monkeypatch):
    from mosaic import assets

    monkeypatch.setattr(assets, "ROOT", tmp_path)
    manifest = {"a": {"kind": "archive", "dest": "runtime/a", "group": "core"}, "b": {"kind": "archive", "dest": "runtime/b", "group": "llm"}}
    monkeypatch.setattr(assets, "load_manifest", lambda: manifest)
    assert assets.missing("core") == ["a"] and assets.missing() == ["a", "b"]
    (tmp_path / "runtime" / "a").mkdir(parents=True)
    (tmp_path / "runtime" / "a" / ".installed").write_text("x")
    assert assets.missing("core") == [] and assets.missing() == ["b"]
