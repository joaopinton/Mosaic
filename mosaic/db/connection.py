"""Accesso a SQLite: una connessione per thread, WAL, chiavi esterne attive, migrazioni numerate."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# Migrazioni successive alla 1 (lo schema iniziale): (versione, script SQL).
MIGRATIONS: list[tuple[int, str]] = [
    (2, """
        -- suggerimenti di persona da confermare (sugg_rejected: id già rifiutati, separati da virgole)
        ALTER TABLE markers ADD COLUMN sugg_subj_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL;
        ALTER TABLE markers ADD COLUMN sugg_dist REAL NOT NULL DEFAULT -1;
        ALTER TABLE markers ADD COLUMN sugg_rejected TEXT NOT NULL DEFAULT '';
        CREATE INDEX idx_markers_sugg ON markers(sugg_subj_id);
        -- i nomi esistenti li ha visti e dati l'utente (pagina Persone): da qui in poi contano come confermati (src manuale)
        UPDATE markers SET src=64 WHERE subj_id IS NOT NULL AND src<64;
    """),
    (3, """
        -- foto tolte dal catalogo dall'utente (D-20): i file restano su disco ma l'indicizzazione non li riaggiunge
        CREATE TABLE excluded_files (
            sha1        TEXT PRIMARY KEY,
            root        TEXT NOT NULL,
            dir         TEXT NOT NULL DEFAULT '',
            name        TEXT NOT NULL,
            size        INTEGER NOT NULL DEFAULT 0,
            mod_time    REAL NOT NULL DEFAULT 0,
            excluded_at TEXT NOT NULL
        );
        CREATE INDEX idx_excluded_path ON excluded_files(root, dir, name);
    """),
    (4, """
        -- misure visive per «Crea selezione» (D-21), calcolate sulla miniatura e rifatte se cambia il file o la versione
        CREATE TABLE photo_aesthetics (
            photo_id    INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
            sha1        TEXT NOT NULL,
            version     INTEGER NOT NULL,
            sharpness   REAL NOT NULL,
            exposure    REAL NOT NULL,
            measured_at TEXT NOT NULL
        );
    """),
    (5, """
        -- progresso strutturato dei job (D-23): quanti elementi fatti su quanti, per barre e tempo rimanente
        ALTER TABLE jobs ADD COLUMN done INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN total INTEGER NOT NULL DEFAULT 0;
    """),
    (6, """
        -- album nato da un altro (una selezione salvata, D-27): «tipo:slug» dell'album di partenza, stabile anche
        -- quando gli album automatici si rigenerano
        ALTER TABLE albums ADD COLUMN source TEXT NOT NULL DEFAULT '';
        CREATE INDEX idx_albums_source ON albums(source);
    """),
    (7, """
        -- nome dato a mano a una cartella o a un momento (D-32): la rigenerazione degli album automatici non lo cambia
        ALTER TABLE albums ADD COLUMN title_manual INTEGER NOT NULL DEFAULT 0;
    """),
]


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _statements(script: str) -> Iterator[str]:
    """Le istruzioni di uno script SQL una alla volta (executescript farebbe COMMIT e uscirebbe dalla transazione)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        if line.strip().startswith("--") and not buffer.strip():
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer
            buffer = ""
    if buffer.strip() and buffer.strip() != ";":
        yield buffer


class Database:
    """Wrapper thread-safe: ogni thread usa la propria connessione sullo stesso file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transazione esplicita: commit in uscita, rollback su eccezione."""
        conn = self.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def migrate(self) -> int:
        """Schema e migrazioni, ognuna in una transazione esclusiva insieme al suo numero di versione.

        Se due Mosaic partono insieme, il secondo aspetta il primo e poi rilegge la versione: niente
        «table already exists» a metà (successo davvero con due doppi clic su Mosaic.exe).
        """
        conn = self.conn()
        steps = [(1, SCHEMA_FILE.read_text(encoding="utf-8")), *MIGRATIONS]
        version = 0
        for target, script in steps:
            conn.execute("BEGIN EXCLUSIVE")
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if target > version:
                    for statement in _statements(script):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={int(target)}")
                    version = target
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return version

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- scorciatoie ---------------------------------------------------------
    def one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        row = self.conn().execute(sql, params).fetchone()
        return row[0] if row else None

    def log_error(self, scope: str, ref: str, message: str) -> None:
        self.conn().execute(
            "INSERT INTO errors(ts, scope, ref, message) VALUES (?,?,?,?)", (utcnow(), scope, ref, message[:2000])
        )
