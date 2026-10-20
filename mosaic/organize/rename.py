"""Rinomina automatica degli originali, resa sicura con le strategie di PhotoPrism.

 - lo schema è configurabile (`{seq:04}_{slug}` per default);
 - si rinomina l'intero stack (JPEG+RAW+XMP+MOV) con lo stesso prefisso, così il raggruppamento resta valido;
 - ogni operazione è scritta nel journal PRIMA di toccare il disco: rollback per lotto o totale e recupero dopo un crash;
 - le collisioni si risolvono con il suffisso `.00001`, come in PhotoPrism;
 - nessun file viene mai sovrascritto.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mosaic.config import RenameConfig
from mosaic.context import Context
from mosaic.db import utcnow
from mosaic.index.related import raw_prefix, stack_key, stack_key_seq
from mosaic.index.store import abs_path, photo_files, primary_file
from mosaic.search import fts
from mosaic.textutil import slugify

log = logging.getLogger(__name__)

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_BASE = 120
SEQ_KEY = "rename_seq_next"


@dataclass(frozen=True)
class RenameOp:
    file_id: int
    photo_id: int
    root: str
    dir: str
    old_name: str
    new_name: str


@dataclass
class RenamePlan:
    ops: list[RenameOp] = field(default_factory=list)
    seqs: dict[int, int] = field(default_factory=dict)
    skipped: list[tuple[int, str]] = field(default_factory=list)

    @property
    def photos(self) -> int:
        return len({op.photo_id for op in self.ops})


@dataclass
class RenameResult:
    batch_id: str
    photos: int = 0
    files: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class RollbackResult:
    files: int = 0
    photos: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)


def sanitize(name: str) -> str:
    """Rende il nome valido su Windows: niente caratteri vietati, punti/spazi finali o nomi riservati."""
    cleaned = INVALID_CHARS.sub("-", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:MAX_BASE].strip(" .")
    if cleaned.lower().split(".")[0] in RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def next_seq(conn: sqlite3.Connection, start: int) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SEQ_KEY,)).fetchone()
    stored = int(row["value"]) if row else start
    top = conn.execute("SELECT COALESCE(MAX(seq),0) FROM photos").fetchone()[0]
    return max(stored, start, top + 1)


def _taken(photo: sqlite3.Row) -> datetime:
    for value in (photo["taken_at_local"], (photo["created_at"] or "").rstrip("Z")):
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
    return datetime.now()


def photo_slug(conn: sqlite3.Connection, photo: sqlite3.Row, slug_max: int) -> tuple[str, str]:
    """(slug, primo nome di etichetta): titolo, poi etichette, poi data."""
    labels = [r["slug"] for r in conn.execute(
        "SELECT l.slug FROM photos_labels pl JOIN labels l ON l.id=pl.label_id WHERE pl.photo_id=? "
        "ORDER BY pl.uncertainty, l.name LIMIT 3", (photo["id"],))]
    title_slug = slugify(photo["title"], slug_max)
    slug = title_slug or slugify("-".join(labels), slug_max) or _taken(photo).strftime("%Y%m%d")
    return slug, (labels[0] if labels else "")


def render_base(cfg: RenameConfig, photo: sqlite3.Row, seq: int, slug: str, label: str, original: str) -> str:
    when = _taken(photo)
    fields = {"seq": seq, "slug": slug, "date": when, "year": when.year, "month": when.month, "day": when.day,
              "label": label, "title": slugify(photo["title"], cfg.slug_max), "name": slugify(original, cfg.slug_max)}
    return sanitize(cfg.pattern.format(**fields)) or "foto"


def _tails(files: list[sqlite3.Row], primary: sqlite3.Row) -> list[tuple[sqlite3.Row, str, str]]:
    """Per ogni file: (riga, coda dopo il prefisso del principale, parte delle estensioni)."""
    primary_raw = raw_prefix(primary["name"])
    out = []
    for f in files:
        raw = raw_prefix(f["name"])
        tail = raw[len(primary_raw):] if raw.lower().startswith(primary_raw.lower()) else ""
        out.append((f, tail, f["name"][len(raw):]))
    return out


def _targets(parts: list[tuple[sqlite3.Row, str, str]], candidate: str) -> list[tuple[sqlite3.Row, str]]:
    """Nuovo nome di ogni file dello stack per un dato prefisso."""
    return [(f, f"{candidate}{tail}{ext}") for f, tail, ext in parts]


def _is_free(parts: list[tuple[sqlite3.Row, str, str]], candidate: str, own: set[tuple[str, str, str]],
             claimed: set[tuple[str, str, str]]) -> bool:
    """Nessun nome di destinazione è già occupato su disco o prenotato da un'altra foto del piano."""
    for f, new in _targets(parts, candidate):
        key = (f["root"], f["dir"], new.lower())
        if key in claimed or (key not in own and abs_path({**dict(f), "name": new}).exists()):
            return False
    return True


def plan_rename(ctx: Context, photo_ids: list[int] | None = None, force: bool = False,
                pattern: str | None = None) -> RenamePlan:
    """Calcola le rinomine senza toccare il disco (anteprima/dry-run)."""
    cfg = ctx.cfg.rename if pattern is None else RenameConfig(**{**ctx.cfg.rename.__dict__, "pattern": pattern})
    conn = ctx.db.conn()
    sql = "SELECT * FROM photos WHERE deleted_at IS NULL"
    params: list = []
    if photo_ids is not None:
        sql += f" AND id IN ({','.join('?' * len(photo_ids))})" if photo_ids else " AND 0"
        params += photo_ids
    if not force:
        sql += " AND seq IS NULL"
    sql += " ORDER BY COALESCE(taken_at_local,''), id"
    plan = RenamePlan()
    counter = next_seq(conn, cfg.seq_start)
    claimed: set[tuple[str, str, str]] = set()
    for photo in conn.execute(sql, params).fetchall():
        files = photo_files(ctx.db, photo["id"])
        primary = primary_file(ctx.db, photo["id"])
        if primary is None or not files:
            plan.skipped.append((photo["id"], "nessun file presente"))
            continue
        seq = photo["seq"] if photo["seq"] is not None else counter
        slug, label = photo_slug(conn, photo, cfg.slug_max)
        base = render_base(cfg, photo, seq, slug, label, primary["original_name"])
        parts = _tails(files, primary)
        own = {(f["root"], f["dir"], f["name"].lower()) for f in files}

        chosen, attempt = base, 0
        while not _is_free(parts, chosen, own, claimed):
            attempt += 1
            chosen = f"{base}.{attempt:05d}"
        ops = [RenameOp(f["id"], photo["id"], f["root"], f["dir"], f["name"], new)
               for f, new in _targets(parts, chosen) if new.lower() != f["name"].lower()]
        if not ops:
            plan.skipped.append((photo["id"], "già rinominata"))
            continue
        for f, new in _targets(parts, chosen):
            claimed.add((f["root"], f["dir"], new.lower()))
        plan.ops.extend(ops)
        plan.seqs[photo["id"]] = seq
        if photo["seq"] is None:
            counter += 1
    return plan


def _refresh_names(conn: sqlite3.Connection, photo_id: int) -> None:
    """Dopo una rinomina le chiavi di stack seguono il nuovo nome del file principale."""
    row = conn.execute("SELECT dir, name FROM files WHERE photo_id=? AND media_type!='sidecar' AND missing=0 "
                       "ORDER BY is_primary DESC, id LIMIT 1", (photo_id,)).fetchone()
    if row:
        conn.execute("UPDATE photos SET stack_key=?, stack_key_seq=?, updated_at=? WHERE id=?",
                     (stack_key(row["dir"], row["name"]), stack_key_seq(row["dir"], row["name"]), utcnow(), photo_id))
    fts.reindex(conn, photo_id)


def _rename_on_disk(root: str, dir_: str, old: str, new: str) -> None:
    base = Path(root) / dir_ if dir_ else Path(root)
    source, target = base / old, base / new
    if not source.exists():
        raise FileNotFoundError(f"file non trovato: {source}")
    if target.exists():
        raise FileExistsError(f"esiste già: {target}")
    os.rename(source, target)


def apply_plan(ctx: Context, plan: RenamePlan, batch_id: str | None = None) -> RenameResult:
    """Esegue la rinomina foto per foto. Se un file dello stack fallisce, annulla le rinomine già fatte per quella foto."""
    recover(ctx)
    batch = batch_id or uuid.uuid4().hex[:10]
    result = RenameResult(batch)
    by_photo: dict[int, list[RenameOp]] = {}
    for op in plan.ops:
        by_photo.setdefault(op.photo_id, []).append(op)
    for photo_id, ops in by_photo.items():
        journal: dict[int, int] = {}
        with ctx.db.tx() as conn:
            for op in ops:
                journal[op.file_id] = conn.execute(
                    "INSERT INTO rename_journal(batch_id, file_id, photo_id, root, old_dir, old_name, new_dir, new_name, ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (batch, op.file_id, photo_id, op.root, op.dir, op.old_name, op.dir, op.new_name, utcnow())).lastrowid
        done: list[RenameOp] = []
        error = ""
        for op in ops:
            try:
                _rename_on_disk(op.root, op.dir, op.old_name, op.new_name)
            except OSError as exc:
                error = f"{op.old_name}: {exc}"
                ctx.db.conn().execute("UPDATE rename_journal SET state='failed', error=? WHERE id=?",
                                      (str(exc)[:500], journal[op.file_id]))
                break
            done.append(op)
            with ctx.db.tx() as conn:
                conn.execute("UPDATE files SET name=? WHERE id=?", (op.new_name, op.file_id))
                conn.execute("UPDATE rename_journal SET state='done' WHERE id=?", (journal[op.file_id],))
        if error:
            for op in reversed(done):
                try:
                    _rename_on_disk(op.root, op.dir, op.new_name, op.old_name)
                    with ctx.db.tx() as conn:
                        conn.execute("UPDATE files SET name=? WHERE id=?", (op.old_name, op.file_id))
                        conn.execute("UPDATE rename_journal SET state='rolled_back' WHERE id=?", (journal[op.file_id],))
                except OSError as exc:
                    ctx.db.log_error("rename", str(op.file_id), f"ripristino non riuscito: {exc}")
            result.failed.append((photo_id, error))
            ctx.db.log_error("rename", str(photo_id), error)
            continue
        with ctx.db.tx() as conn:
            conn.execute("UPDATE photos SET seq=? WHERE id=?", (plan.seqs[photo_id], photo_id))
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (SEQ_KEY, str(max(plan.seqs.values()) + 1)))
            _refresh_names(conn, photo_id)
        result.photos += 1
        result.files += len(done)
    return result


def rollback(ctx: Context, batch_id: str | None = None) -> RollbackResult:
    """Riporta i file ai nomi di prima: un lotto, oppure tutti i lotti se `batch_id` è None."""
    result = RollbackResult()
    sql = "SELECT * FROM rename_journal WHERE state='done'"
    params: tuple = ()
    if batch_id:
        sql += " AND batch_id=?"
        params = (batch_id,)
    rows = ctx.db.all(sql + " ORDER BY id DESC", params)
    touched: set[int] = set()
    for row in rows:
        try:
            _rename_on_disk(row["root"], row["new_dir"], row["new_name"], row["old_name"])
        except OSError as exc:
            result.failed.append((row["file_id"], str(exc)))
            ctx.db.conn().execute("UPDATE rename_journal SET error=? WHERE id=?", (str(exc)[:500], row["id"]))
            continue
        with ctx.db.tx() as conn:
            conn.execute("UPDATE files SET name=? WHERE id=?", (row["old_name"], row["file_id"]))
            conn.execute("UPDATE rename_journal SET state='rolled_back', error='' WHERE id=?", (row["id"],))
        touched.add(row["photo_id"])
        result.files += 1
    with ctx.db.tx() as conn:
        for photo_id in touched:
            remaining = conn.execute("SELECT COUNT(*) FROM rename_journal WHERE photo_id=? AND state='done'",
                                     (photo_id,)).fetchone()[0]
            if remaining == 0:
                conn.execute("UPDATE photos SET seq=NULL WHERE id=?", (photo_id,))
            _refresh_names(conn, photo_id)
            result.photos += 1
    return result


def recover(ctx: Context) -> int:
    """Dopo un'interruzione, chiude le righe 'planned': se il file è già stato rinominato la registra, altrimenti la segna fallita."""
    fixed = 0
    for row in ctx.db.all("SELECT * FROM rename_journal WHERE state='planned'"):
        base = Path(row["root"]) / row["new_dir"] if row["new_dir"] else Path(row["root"])
        renamed = (base / row["new_name"]).exists() and not (base / row["old_name"]).exists()
        with ctx.db.tx() as conn:
            if renamed:
                conn.execute("UPDATE files SET name=? WHERE id=?", (row["new_name"], row["file_id"]))
                conn.execute("UPDATE rename_journal SET state='done' WHERE id=?", (row["id"],))
            else:
                conn.execute("UPDATE rename_journal SET state='failed', error='interrotta' WHERE id=?", (row["id"],))
        fixed += 1
    return fixed


def auto_rename(ctx: Context, photo_ids: list[int] | None = None) -> RenameResult | None:
    """Rinomina le foto non ancora rinominate se la rinomina automatica è attiva."""
    if not ctx.cfg.rename.auto:
        return None
    plan = plan_rename(ctx, photo_ids)
    return apply_plan(ctx, plan) if plan.ops else RenameResult(batch_id="")
