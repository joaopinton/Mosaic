"""Handler dei job e catena automatica della pipeline.

indicizza → elabora → analizza (SigLIP, etichette, volti rilevati) → volti (clustering) → moments
→ didascalie LLM (se attive e installate) → rinomina automatica.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mosaic.context import Context
from mosaic.index.indexer import index_roots
from mosaic.index.store import abs_path, primary_file
from mosaic.jobs.queue import Job, JobQueue
from mosaic.jobs.worker import Handler, JobControl

log = logging.getLogger(__name__)

CHUNK = 25


def _chunks(ids: list[int], size: int = CHUNK) -> list[list[int]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def unprocessed_ids(ctx: Context) -> list[int]:
    """Foto indicizzate ma mai elaborate (per esempio dopo un'interruzione)."""
    return [r["id"] for r in ctx.db.all(
        "SELECT id FROM photos WHERE deleted_at IS NULL AND meta_at IS NULL ORDER BY id")]


def _after_analysis(ctx: Context, ctl: JobControl) -> None:
    ctl.submit("faces" if ctx.cfg.ai.faces else "moments", unique=True)


def _after_moments(ctx: Context, ctl: JobControl) -> None:
    from mosaic.llm.backends import captioning_available

    if ctx.cfg.llm.mode == "newly-indexed" and captioning_available(ctx):
        ctl.submit("caption", {}, unique=True)
    else:
        ctl.submit("rename", {}, unique=True)


def h_index(ctx: Context, job: Job, ctl: JobControl) -> None:
    payload = job.payload
    known = _known_files(ctx, payload.get("roots"))  # stima del totale: i file già nel catalogo (0 = ancora ignoto)

    def scanned(stage: str, n: int, total: int) -> None:
        estimate = max(known, n) if known else 0
        ctl.progress(min(0.99, n / estimate) if estimate else 0.0, f"{n} file esaminati", n, estimate)

    stats = index_roots(ctx, payload.get("roots"), bool(payload.get("rescan")), progress=scanned, cancel=ctl.cancel)
    synced = ""
    # solo dopo un giro su tutte le cartelle: un file spostato in un'altra cartella va ritrovato, non cancellato
    if stats.missing and ctx.cfg.delete_mode == "trash" and not payload.get("roots") and not ctl.cancel.is_set():
        from mosaic.organize.catalog import sync_deleted

        gone = sync_deleted(ctx)  # modo «Sincronizzata» (D-24): cancellati dal computer, cancellati da Mosaic
        synced = f", {gone.photos} tolte perché cancellate dal computer" if gone.photos else ""
    ids = sorted(set(stats.changed_photo_ids) | set(unprocessed_ids(ctx)))
    for chunk in _chunks(ids):
        ctl.submit("process", {"ids": chunk})
    if not ids and not ctl.cancel.is_set():
        ctl.submit("analyze", {}, unique=True)
    ctl.progress(1.0, f"{stats.new_photos} nuove foto, {stats.duplicates} duplicati, {stats.moved} spostate, "
                      f"{stats.missing} mancanti{synced}", stats.scanned, stats.scanned)


def _known_files(ctx: Context, roots: list[str] | None) -> int:
    from mosaic.organize.catalog import same_root

    chosen = list(roots or ctx.cfg.library_roots)
    rows = ctx.db.all("SELECT root, COUNT(*) AS n FROM files WHERE missing=0 GROUP BY root")
    return sum(r["n"] for r in rows if any(same_root(r["root"], c) for c in chosen))


def h_process(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.pipeline import process_photo

    ids = list(job.payload.get("ids", []))
    for n, pid in enumerate(ids, 1):
        if ctl.cancel.is_set():
            return
        try:
            process_photo(ctx, pid)
        except Exception as exc:  # una foto rotta non ferma le altre
            ctx.db.log_error("process", str(pid), f"{type(exc).__name__}: {exc}")
        ctl.progress(n / max(len(ids), 1), f"{n}/{len(ids)} foto elaborate", n, len(ids))
    if JobQueue(ctx.db).pending("process") <= 1 and ctx.cfg.ai.enabled:
        ctl.submit("analyze", {}, unique=True)  # era l'ultimo job di elaborazione
    elif not ctx.cfg.ai.enabled and JobQueue(ctx.db).pending("process") <= 1:
        ctl.submit("moments", {}, unique=True)


def h_analyze(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.ai.analyze import analyze_photos, pending_photo_ids, release_models
    from mosaic.ai.labels import prune_for_context
    from mosaic.ai.siglip import ModelMissing
    from mosaic.llm.server import stop_llm

    stop_llm(ctx)  # RAM: l'LLM non convive con SigLIP
    prune_for_context(ctx)  # etichette tolte dal vocabolario spariscono anche dalle foto già analizzate
    ids = job.payload.get("ids") or pending_photo_ids(ctx, bool(job.payload.get("force")))
    try:
        stats = analyze_photos(ctx, ids, ctl)
        ctl.progress(1.0, f"{stats.photos} analizzate, {stats.labels} etichette, {stats.faces} volti")
    except ModelMissing as exc:  # senza modello il resto della catena (album, rinomina) deve comunque proseguire
        ctx.db.log_error("analyze", "", str(exc))
        ctl.progress(1.0, "analisi AI saltata: modello mancante")
    finally:
        release_models(ctx)  # a fine analisi la RAM dei modelli torna libera per le fasi successive
    if not ctl.cancel.is_set():
        _after_analysis(ctx, ctl)


def h_faces(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.ai.analyze import release_models
    from mosaic.ai.face_worker import FacesCancelled, pending_reembed, reembed_markers, run_faces
    from mosaic.ai.siglip import ModelMissing

    if pending_reembed(ctx):  # cambio di modello: i vettori si ricalcolano prima di raggruppare
        try:
            reembed_markers(ctx, progress=ctl.progress, is_cancelled=ctl.cancel.is_set)
        except ModelMissing as exc:
            ctx.db.log_error("faces", "", str(exc))
            ctl.progress(1.0, "volti saltati: modello mancante")
            return
        finally:
            release_models(ctx)
        if ctl.cancel.is_set():
            return
    try:
        stats = run_faces(ctx, ctl.cancel.is_set)
    except FacesCancelled:
        return
    ctl.progress(1.0, f"{stats.clusters + stats.retry_clusters} cluster nuovi, {stats.matched + stats.subjects} volti riconosciuti, "
                      f"{stats.suggested} da confermare")
    ctl.submit("moments", {}, unique=True)


def h_moments(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import moments

    stats = moments.generate(ctx)
    ctl.progress(1.0, f"album automatici: {stats.created} nuovi, {stats.updated} aggiornati, {stats.removed} rimossi")
    if not ctl.cancel.is_set():
        _after_moments(ctx, ctl)


def h_caption(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.llm.worker import caption_photos, pending_caption_ids

    ids = job.payload.get("ids") or pending_caption_ids(ctx, bool(job.payload.get("force")), job.payload.get("limit"))
    stats = caption_photos(ctx, ids, ctl)
    ctl.progress(1.0, f"{stats.photos} didascalie{stats.where()}, {stats.failed} non riuscite")
    if not ctl.cancel.is_set():
        ctl.submit("rename", {}, unique=True)


def h_rename(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import rename

    ctl.submit("backup", {}, unique=True)  # a fine catena (o dopo una rinomina) i dati manuali si salvano in YAML
    if not (ctx.cfg.rename.auto or job.payload.get("apply")):
        ctl.progress(1.0, "rinomina automatica disattivata")
        return
    plan = rename.plan_rename(ctx, job.payload.get("ids"), bool(job.payload.get("force")), job.payload.get("pattern"))
    result = rename.apply_plan(ctx, plan) if plan.ops else rename.RenameResult(batch_id="")
    ctl.progress(1.0, f"{result.photos} foto rinominate (lotto {result.batch_id or '-'}), {len(result.failed)} errori")


def h_backup(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import backup

    written = backup.backup_changed(ctx)
    ctl.progress(1.0, f"{written} backup YAML aggiornati")


def h_rollback(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import rename

    result = rename.rollback(ctx, job.payload.get("batch_id"))
    ctl.progress(1.0, f"{result.files} file ripristinati, {len(result.failed)} errori")


def h_transcode(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.video import probe, transcode

    main = primary_file(ctx.db, int(job.payload["photo_id"]))
    if main is None or main["media_type"] != "video":
        return
    path = abs_path(main)
    info = probe.probe(path)
    if info is not None and not transcode.is_web_playable(info, path.suffix):
        transcode.transcode(ctx, path, main["sha1"], info.duration, cancel=ctl.cancel)


def h_import(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize.importer import import_files

    stats = import_files(ctx, Path(job.payload["source"]), bool(job.payload.get("move")))
    for chunk in _chunks(stats.photo_ids):
        ctl.submit("process", {"ids": chunk})
    ctl.progress(1.0, f"{stats.imported} importate, {stats.skipped} già presenti, {len(stats.errors)} errori")


def h_drop(ctx: Context, job: Job, ctl: JobControl) -> None:
    """File rilasciati o caricati (D-25): copie nella libreria, zip estratti, poi la solita pipeline."""
    from mosaic.organize.dropin import run_drop

    stats = run_drop(ctx, job.payload, progress=lambda done, total, label: ctl.progress(done / max(total, 1), label, done, total))
    for error in stats.errors:
        ctx.db.log_error("drop", "", error)
    for chunk in _chunks(stats.photo_ids):
        ctl.submit("process", {"ids": chunk})
    ctl.progress(1.0, f"{stats.imported} aggiunte, {stats.skipped} già presenti, {len(stats.errors)} errori")


def h_poster(ctx: Context, job: Job, ctl: JobControl) -> None:
    """Poster a mosaico (D-26): un riquadro alla volta, con il progresso; il file va nella cartella Immagini/Mosaic."""
    from mosaic.organize import poster

    payload = job.payload
    out = poster.poster_path(Path(payload.get("dir") or poster.posters_dir()), str(payload["name"]))
    _, dpi = poster.render(ctx, list(payload["uids"]), poster.Options(**payload.get("options", {})), out,
                           progress=lambda done, total: ctl.progress(done / max(total, 1), f"{done}/{total} riquadri", done, total))
    ctl.progress(1.0, f"Poster salvato in {out} ({dpi} dpi)")


def h_export(ctx: Context, job: Job, ctl: JobControl) -> None:
    """Foto copiate (o migliorate) in una cartella a scelta (D-30), una alla volta con il progresso."""
    from mosaic.organize import enhance, export_photos

    payload = job.payload
    look = payload.get("enhance")
    opts = export_photos.Options(raw=bool(payload.get("raw")), videos=bool(payload.get("videos", True)),
                                 enhance=enhance.Options(**look) if look else None)
    result = export_photos.export(ctx, list(payload["uids"]), Path(payload["dest"]), opts,
                                  progress=lambda done, total, label: ctl.progress(done / max(total, 1), label, done, total),
                                  cancel=ctl.cancel)
    for error in result.errors:
        ctx.db.log_error("export", "", error)
    better = f", {result.enhanced} migliorate" if result.enhanced else ""
    problems = f", {len(result.errors)} con problemi" if result.errors else ""
    ctl.progress(1.0, f"{result.copied} foto esportate in {result.folder}{better}{problems}")


def h_cleanup(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import cleanup

    stats = cleanup.cleanup(ctx, bool(job.payload.get("purge")))
    ctl.progress(1.0, f"{stats.marked_missing} mancanti, {stats.restored} ritrovati, {stats.purged_photos} foto eliminate")


def h_unlink(ctx: Context, job: Job, ctl: JobControl) -> None:
    from mosaic.organize import catalog

    stats = catalog.unlink_root(ctx, str(job.payload["root"]))
    ctl.progress(1.0, f"cartella scollegata: {stats.photos} foto e {stats.files} file tolti dal catalogo (i file su disco restano)")
    ctl.submit("backup", {}, unique=True)


def build_handlers() -> dict[str, Handler]:
    return {
        "index": h_index, "drop": h_drop, "poster": h_poster, "export": h_export, "process": h_process, "analyze": h_analyze, "faces": h_faces, "moments": h_moments,
        "caption": h_caption, "rename": h_rename, "rollback": h_rollback, "transcode": h_transcode,
        "import": h_import, "cleanup": h_cleanup, "backup": h_backup, "unlink": h_unlink,
    }
