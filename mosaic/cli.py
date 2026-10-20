"""Riga di comando di Mosaic: `python -m mosaic <comando>`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from mosaic import config as config_mod
from mosaic.search.parser import QueryError
from mosaic.service import Mosaic


def _print(*parts: object) -> None:
    print(*parts, flush=True)


def run_job(m: Mosaic, job_type: str, payload: dict | None = None, timeout: float = 24 * 3600) -> bool:
    """Accoda un job e aspetta che la coda (catena compresa) si svuoti, mostrando l'avanzamento."""
    events = m.bus.subscribe()
    m.runner.start()
    m.runner.submit(job_type, payload or {})
    end = time.monotonic() + timeout
    last = ""
    try:
        while time.monotonic() < end:
            try:
                event = events.get(timeout=0.5)
            except Exception:
                if m.runner.queue.pending() == 0:
                    return m.ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE state='failed'") == 0
                continue
            line = f"[{event.get('type')}] {event.get('state')} {event.get('message') or ''}".strip()
            if event.get("error"):
                line += f"  ERRORE: {event['error']}"
            if line != last and (event.get("state") != "running" or event.get("message")):
                _print(line)
                last = line
            if m.runner.queue.pending() == 0:
                return m.ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE state='failed'") == 0
        return False
    finally:
        m.bus.unsubscribe(events)
        m.runner.stop()


def cmd_index(m: Mosaic, a: argparse.Namespace) -> int:
    roots = [str(Path(p).resolve()) for p in a.paths] or None
    if roots:
        known = {str(Path(r).resolve()) for r in m.cfg.library_roots}
        new = [r for r in roots if r not in known]
        if new:
            m.reload_config(config_mod.update(m.cfg, {"library_roots": [*m.cfg.library_roots, *new]}))
            _print(f"aggiunte alla libreria: {', '.join(new)}")
    if not m.cfg.library_roots:
        _print("nessuna cartella della libreria: usa `mosaic index <cartella>` oppure `mosaic config add-root <cartella>`")
        return 2
    if a.no_ai:
        m.reload_config(config_mod.update(m.cfg, {"ai": {"enabled": False}, "llm": {"mode": "never"}}))
    return 0 if run_job(m, "index", {"rescan": a.rescan}) else 1


def cmd_search(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.search.query import search

    try:
        result = search(m.ctx, " ".join(a.query), count=a.top, offset=a.offset)
    except QueryError as exc:
        _print(f"query non valida: {exc}")
        return 2
    _print(f"{result.total} risultati" + (" (ricerca semantica)" if result.semantic else ""))
    for p in result.photos:
        title = p["title"] or "(senza titolo)"
        _print(f"  {p['uid']}  {p['taken_at_local'] or '—':19}  {p['type']:8}  {title}")
    return 0


def _resolve_photo(m: Mosaic, term: str) -> int | None:
    row = m.ctx.db.one("SELECT id FROM photos WHERE uid=?", (term,))
    if row:
        return row["id"]
    row = m.ctx.db.one(
        "SELECT p.id FROM photos p JOIN files f ON f.photo_id=p.id WHERE f.name LIKE ? ESCAPE '\\' OR f.original_name LIKE ? "
        "ESCAPE '\\' OR p.title LIKE ? ESCAPE '\\' ORDER BY p.id LIMIT 1", (f"%{term}%",) * 3)
    return row["id"] if row else None


def cmd_similar(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.search.similar import similar_photos

    pid = _resolve_photo(m, a.term)
    if pid is None:
        _print(f"nessuna foto corrisponde a «{a.term}»")
        return 2
    hits = similar_photos(m.ctx, pid, a.top)
    if not hits:
        _print("nessun risultato: la foto non ha ancora un embedding (esegui `mosaic analyze`)")
        return 1
    for h in hits:
        row = m.ctx.db.one("SELECT uid, title FROM photos WHERE id=?", (h.photo_id,))
        _print(f"  {h.score:.2f}  {row['uid']}  {row['title'] or '(senza titolo)'}")
        for axis, reasons in h.reasons.items():
            _print(f"        {axis}: {'; '.join(reasons)}")
    return 0


def cmd_rename(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.organize import rename

    plan = rename.plan_rename(m.ctx, force=a.force, pattern=a.pattern)
    _print(f"{plan.photos} foto, {len(plan.ops)} file da rinominare; {len(plan.skipped)} saltate")
    for op in plan.ops[: (None if a.dry_run else 10)]:
        _print(f"  {op.dir + '/' if op.dir else ''}{op.old_name}  →  {op.new_name}")
    if a.dry_run or not plan.ops:
        return 0
    result = rename.apply_plan(m.ctx, plan)
    _print(f"fatto: {result.photos} foto, {result.files} file (lotto {result.batch_id}); errori: {len(result.failed)}")
    for photo_id, error in result.failed:
        _print(f"  foto {photo_id}: {error}")
    return 0 if not result.failed else 1


def cmd_rollback(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.organize import rename

    if not a.batch and not a.all:
        _print("indica --batch ID oppure --all")
        return 2
    result = rename.rollback(m.ctx, None if a.all else a.batch)
    _print(f"ripristinati {result.files} file di {result.photos} foto; errori: {len(result.failed)}")
    return 0 if not result.failed else 1


def cmd_import(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.organize.importer import ImportFailed, import_files

    try:
        stats = import_files(m.ctx, Path(a.source), a.move)
    except ImportFailed as exc:
        _print(f"import impossibile: {exc}")
        return 2
    _print(f"importate {stats.imported}, già presenti {stats.skipped}, errori {len(stats.errors)}")
    for error in stats.errors:
        _print(f"  {error}")
    if stats.photo_ids:
        from mosaic.jobs.handlers import _chunks

        events_ok = True
        for chunk in _chunks(stats.photo_ids):
            events_ok &= run_job(m, "process", {"ids": chunk})
        return 0 if events_ok else 1
    return 0


def cmd_legacy(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.organize.legacy import LegacyError, import_legacy

    try:
        stats = import_legacy(m.ctx, Path(a.db), a.album)
    except LegacyError as exc:
        _print(f"import non riuscito: {exc}")
        return 2
    _print(f"{stats.matched}/{stats.total} foto abbinate per SHA-256 → album «{stats.album}»")
    if stats.unmatched:
        _print(f"{len(stats.unmatched)} non trovate (indicizza prima la cartella con le foto): " + ", ".join(stats.unmatched[:5]) + (" …" if len(stats.unmatched) > 5 else ""))
    return 0


def cmd_export(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.organize.export import export_catalog

    n = export_catalog(m.ctx, a.format, Path(a.out), a.private)
    _print(f"esportate {n} foto in {a.out}")
    return 0


def cmd_status(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.assets import status

    db = m.ctx.db
    _print(f"libreria: {', '.join(m.cfg.library_roots) or '(nessuna cartella)'}")
    _print(f"foto: {db.scalar('SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL')}  file: {db.scalar('SELECT COUNT(*) FROM files')}  "
           f"analizzate: {db.scalar('SELECT COUNT(*) FROM photos WHERE analyzed_at IS NOT NULL')}  "
           f"persone: {db.scalar('SELECT COUNT(*) FROM subjects')}  album: {db.scalar('SELECT COUNT(*) FROM albums')}")
    _print("job: " + (", ".join(f"{k}={v}" for k, v in m.runner.queue.counts().items()) or "nessuno"))
    _print("asset: " + ", ".join(f"{k}={'ok' if v else 'MANCA'}" for k, v in status().items()))
    return 0


def cmd_config(m: Mosaic, a: argparse.Namespace) -> int:
    if a.action == "show":
        _print(json.dumps(config_mod.to_dict(m.cfg), indent=2, ensure_ascii=False))
        return 0
    if a.action == "add-root":
        root = str(Path(a.value).resolve())
        if not Path(root).is_dir():
            _print(f"cartella non trovata: {root}")
            return 2
        if root not in m.cfg.library_roots:
            m.reload_config(config_mod.update(m.cfg, {"library_roots": [*m.cfg.library_roots, root]}))
        _print(f"libreria: {', '.join(m.cfg.library_roots)}")
        return 0
    return 2


def cmd_simple_job(job_type: str):  # type: ignore[no-untyped-def]
    def run(m: Mosaic, a: argparse.Namespace) -> int:
        payload: dict = {}
        for key in ("force", "limit", "purge"):
            if getattr(a, key, None):
                payload[key] = getattr(a, key)
        if job_type == "rename":
            payload["apply"] = True
        return 0 if run_job(m, job_type, payload) else 1
    return run


def cmd_serve(m: Mosaic, a: argparse.Namespace) -> int:
    from mosaic.app import serve

    m.stop()
    return serve(port=a.port, open_browser=not a.no_browser)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mosaic", description="Mosaic 2: gestore di foto e video, tutto in locale.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="avvia l'interfaccia web")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("index", help="indicizza la libreria e lancia l'intera pipeline")
    s.add_argument("paths", nargs="*")
    s.add_argument("--rescan", action="store_true", help="rielabora anche i file invariati")
    s.add_argument("--no-ai", action="store_true", help="solo indicizzazione, metadati e miniature")
    s.set_defaults(func=cmd_index)

    for name, help_text in (("analyze", "embedding, etichette e volti"), ("faces", "clustering dei volti"),
                            ("moments", "album automatici"), ("caption", "didascalie con l'LLM locale"),
                            ("cleanup", "file mancanti e orfani")):
        s = sub.add_parser(name, help=help_text)
        if name in ("analyze", "caption"):
            s.add_argument("--force", action="store_true", help="rifai anche le foto già elaborate")
        if name == "caption":
            s.add_argument("--limit", type=int)
        if name == "cleanup":
            s.add_argument("--purge", action="store_true", help="elimina davvero le righe dei file mancanti")
        s.set_defaults(func=cmd_simple_job(name))

    s = sub.add_parser("search", help="cerca (testo libero e filtri chiave:valore)")
    s.add_argument("query", nargs="+")
    s.add_argument("-k", "--top", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("similar", help="foto simili a una data (uid, nome o titolo)")
    s.add_argument("term")
    s.add_argument("-k", "--top", type=int, default=6)
    s.set_defaults(func=cmd_similar)

    s = sub.add_parser("rename", help="rinomina i file (usa --dry-run per l'anteprima)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--pattern")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("rollback", help="annulla le rinomine")
    s.add_argument("--batch")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_rollback)

    s = sub.add_parser("import", help="importa una cartella esterna nella libreria")
    s.add_argument("source")
    s.add_argument("--move", action="store_true", help="sposta invece di copiare")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("import-legacy", help="importa il catalogo del vecchio Mosaic (SHA-256)")
    s.add_argument("db")
    s.add_argument("--album", default="Padova Moto Days 2026")
    s.set_defaults(func=cmd_legacy)

    s = sub.add_parser("export", help="esporta il catalogo")
    s.add_argument("--format", choices=["json", "csv"], default="json")
    s.add_argument("--out", required=True)
    s.add_argument("--private", action="store_true", help="includi le foto private")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("status", help="stato della libreria, dei job e degli asset")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("config", help="mostra o modifica la configurazione")
    s.add_argument("action", choices=["show", "add-root"])
    s.add_argument("value", nargs="?")
    s.set_defaults(func=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from mosaic.app import serve

        return serve(port=args.port, open_browser=not args.no_browser)
    m = Mosaic.open()
    try:
        return args.func(m, args)
    finally:
        m.stop()


if __name__ == "__main__":
    raise SystemExit(main())
