"""Coda di job persistente in SQLite: sopravvive ai crash (i job 'running' tornano in coda all'avvio)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from mosaic.db import Database, utcnow

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0


def _job(row: sqlite3.Row) -> Job:
    return Job(id=row["id"], type=row["type"], payload=json.loads(row["payload"] or "{}"), attempts=row["attempts"])


class JobQueue:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, job_type: str, payload: dict[str, Any] | None = None, unique: bool = False) -> int:
        """Aggiunge un job. Con unique=True, se ne esiste già uno in coda dello stesso tipo e payload, lo riusa."""
        body = json.dumps(payload or {}, sort_keys=True)
        now = utcnow()
        with self.db.tx() as conn:
            if unique:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE type=? AND payload=? AND state=?", (job_type, body, QUEUED)
                ).fetchone()
                if row:
                    return row["id"]
            return conn.execute(
                "INSERT INTO jobs(type, payload, created_at, updated_at) VALUES (?,?,?,?)", (job_type, body, now, now)
            ).lastrowid

    def claim(self, allowed: set[str] | None = None, excluded: set[str] | None = None) -> Job | None:
        """Prende il job in coda più vecchio tra i tipi consentiti (e non esclusi) e lo marca come in esecuzione."""
        with self.db.tx() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE state=? ORDER BY id LIMIT 200", (QUEUED,)).fetchall()
            for row in rows:
                if allowed is not None and row["type"] not in allowed:
                    continue
                if excluded and row["type"] in excluded:
                    continue
                now = utcnow()
                conn.execute(
                    "UPDATE jobs SET state=?, attempts=attempts+1, started_at=?, updated_at=? WHERE id=?",
                    (RUNNING, now, now, row["id"]),
                )
                job = _job(row)
                return Job(id=job.id, type=job.type, payload=job.payload, attempts=job.attempts + 1)
        return None

    def progress(self, job_id: int, progress: float, message: str = "", done: int | None = None,
                 total: int | None = None) -> None:
        """Avanzamento 0..1 con un messaggio; `done`/`total` (elementi fatti su quanti) solo quando si conoscono."""
        self.db.conn().execute(
            "UPDATE jobs SET progress=?, message=?, done=COALESCE(?, done), total=COALESCE(?, total), updated_at=? "
            "WHERE id=? AND state=?",
            (max(0.0, min(1.0, progress)), message[:300], done, total, utcnow(), job_id, RUNNING),
        )

    def finish(self, job_id: int, state: str, error: str = "") -> None:
        now = utcnow()
        self.db.conn().execute(
            "UPDATE jobs SET state=?, error=?, finished_at=?, updated_at=?, progress=CASE WHEN ?='done' THEN 1 ELSE progress END "
            "WHERE id=?",
            (state, error[:2000], now, now, state, job_id),
        )

    def requeue(self, job_id: int, error: str) -> None:
        self.db.conn().execute(
            "UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?", (QUEUED, error[:2000], utcnow(), job_id)
        )

    def cancel(self, job_id: int) -> bool:
        cur = self.db.conn().execute(
            "UPDATE jobs SET state=?, finished_at=?, updated_at=? WHERE id=? AND state=?",
            (CANCELLED, utcnow(), utcnow(), job_id, QUEUED),
        )
        return cur.rowcount > 0

    def reset_running(self) -> int:
        """Dopo un crash i job rimasti 'running' tornano in coda."""
        cur = self.db.conn().execute(
            "UPDATE jobs SET state=?, updated_at=? WHERE state=?", (QUEUED, utcnow(), RUNNING)
        )
        return cur.rowcount

    def get(self, job_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def list(self, limit: int = 50) -> list[sqlite3.Row]:
        """Tutti i job attivi (anche centinaia di blocchi in coda) più gli ultimi `limit` finiti, dal più recente."""
        return self.db.all(
            "SELECT * FROM jobs WHERE state IN ('queued','running') UNION ALL "
            "SELECT * FROM (SELECT * FROM jobs WHERE state NOT IN ('queued','running') ORDER BY id DESC LIMIT ?) "
            "ORDER BY id DESC", (limit,))

    def counts(self) -> dict[str, int]:
        rows = self.db.all("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    def pending(self, job_type: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')"
        params: tuple[Any, ...] = ()
        if job_type:
            sql += " AND type=?"
            params = (job_type,)
        return int(self.db.scalar(sql, params))

    def purge_finished(self, keep: int = 200) -> int:
        cur = self.db.conn().execute(
            "DELETE FROM jobs WHERE state IN ('done','failed','cancelled') AND id NOT IN "
            "(SELECT id FROM jobs ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        return cur.rowcount
