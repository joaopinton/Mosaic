"""Confronta modelli, dimensioni dell'immagine e richieste in parallelo per le didascalie.

Misura secondi per foto (ritmo reale con N richieste insieme), dove va il tempo (prompt e immagine contro risposta,
dai `timings` di llama-server), RAM di picco e testo prodotto. Non scrive nulla nel database: legge solo le miniature.
Il risultato va in <data_dir>/bench/captions.md. Non lanciarlo mentre gira il job delle didascalie: due llama-server
insieme non stanno in 8 GB di RAM.

    python scripts/bench_captions.py                                    # 15 foto, tutti i modelli installati, lati 720 e 448
    python scripts/bench_captions.py --count 8 --sizes 448 384
    python scripts/bench_captions.py --models qwen3vl-2b --parallel 1 2 3   # quante richieste insieme convengono
    python scripts/bench_captions.py --server-dir runtime/llama-vulkan   # prova un'altra build di llama.cpp
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mosaic import config as config_mod
from mosaic.ai.registry import llm_spec
from mosaic.context import Context
from mosaic.llm import captions, context
from mosaic.llm.captions import LlmError, Timings
from mosaic.llm.server import LlamaServer, LlmUnavailable
from mosaic.llm.worker import people_in_photo, photo_context
from mosaic.pipeline import thumb_source

DEFAULT_MODELS = ("qwen25vl-3b", "qwen3vl-2b")
DEFAULT_SIZES = (720, 448)


@dataclass(frozen=True)
class Answer:
    seconds: float
    text: str
    timings: Timings | None = None
    failed: bool = False


@dataclass
class Result:
    model: str
    size: int
    parallel: int
    startup: float = 0.0
    wall: float = 0.0
    peak_mb: int | None = None
    answers: dict[int, Answer] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.model} @ {self.size} ×{self.parallel}"

    @property
    def failures(self) -> int:
        return sum(a.failed for a in self.answers.values())

    @property
    def timings(self) -> list[Timings]:
        return [a.timings for a in self.answers.values() if a.timings is not None]


def pick_photos(ctx: Context, count: int) -> list[int]:
    """Foto distribuite in modo uniforme nella libreria, così il campione non è solo l'inizio."""
    ids = [r["id"] for r in ctx.db.all("SELECT id FROM photos WHERE deleted_at IS NULL AND meta_at IS NOT NULL ORDER BY id")]
    if len(ids) <= count:
        return ids
    step = len(ids) / count
    return [ids[int(i * step)] for i in range(count)]


def peak_working_set_mb(pid: int) -> int | None:
    """Picco di RAM del processo (PeakWorkingSet64) letto con PowerShell."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).PeakWorkingSet64"],
            capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        return round(int(out) / 1024 / 1024)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def prepare_requests(ctx: Context, photos: list[int], size: int) -> list[tuple[int, bytes, list[str], str]]:
    """Immagine, persone e contesto di ogni foto, come nel job vero."""
    stories = context.load_stories(ctx)
    out = []
    for pid in photos:
        path = thumb_source(ctx, pid, "fit_720")
        if path is not None:
            out.append((pid, captions.prepare_image(path, size), people_in_photo(ctx.db.conn(), pid),
                        photo_context(ctx, stories, pid)))
    return out


def ask(base_url: str, client: httpx.Client, language: str, image: bytes, people: list[str], note: str) -> Answer:
    started = time.monotonic()
    try:
        cap = captions.describe(base_url, image, language, client, people, note)
    except LlmError as exc:
        return Answer(time.monotonic() - started, f"ERRORE: {exc}", failed=True)
    text = f"{cap.title} — {cap.caption} [{', '.join(cap.keywords)}]"
    return Answer(time.monotonic() - started, text, cap.timings)


def run_variant(ctx: Context, model: str, size: int, parallel: int, photos: list[int],
                server_dir: str | None) -> Result | None:
    spec = llm_spec(ctx.models_dir, model)
    if server_dir:
        spec = replace(spec, server=server_dir)
    ctx.cfg = replace(ctx.cfg, llm=replace(ctx.cfg.llm, parallel=parallel))  # il server parte con uno slot per richiesta
    server = LlamaServer(ctx, spec=spec)
    if not server.available:
        print(f"[{model}] non installato: salto (python -m mosaic.assets fetch --group llm)")
        return None
    result = Result(model, size, parallel)
    requests = prepare_requests(ctx, photos, size)
    started = time.monotonic()
    try:
        base_url = server.ensure()
    except LlmUnavailable as exc:
        print(f"[{model}] non parte: {exc}")
        return None
    result.startup = time.monotonic() - started
    print(f"[{result.label}] avviato in {result.startup:.0f} s")
    try:
        started = time.monotonic()
        with httpx.Client(timeout=captions.REQUEST_TIMEOUT) as client, ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(ask, base_url, client, ctx.cfg.llm.language, image, people, note): pid
                       for pid, image, people, note in requests}
            for n, fut in enumerate(as_completed(futures), 1):
                answer = result.answers[futures[fut]] = fut.result()
                detail = answer.timings.summary() if answer.timings else answer.text
                print(f"  {n}/{len(requests)}  {answer.seconds:.1f} s  {detail}", flush=True)
        result.wall = time.monotonic() - started
        if server._proc is not None:
            result.peak_mb = peak_working_set_mb(server._proc.pid)
    finally:
        server.stop()
    return result


def _median(values: list[float], fmt: str = "{:.0f}") -> str:
    return fmt.format(statistics.median(values)) if values else "-"


def _row(r: Result) -> str:
    t = r.timings
    pace = f"{r.wall / len(r.answers):.1f}" if r.answers else "-"
    output_s = sum(x.output_s for x in t)
    rate = f"{sum(x.output_tokens for x in t) / output_s:.1f}" if output_s > 0 else "-"
    ram = f"{r.peak_mb} MB" if r.peak_mb else "n/d"
    return (f"| {r.model} | {r.size} | {r.parallel} | {r.startup:.0f} s | **{pace}** | "
            f"{_median([a.seconds for a in r.answers.values()], '{:.1f}')} | "
            f"{_median([x.prompt_tokens for x in t])} ({_median([x.cached_tokens for x in t])}) | "
            f"{_median([x.prompt_s for x in t], '{:.1f}')} | {_median([x.output_tokens for x in t])} | "
            f"{_median([x.output_s for x in t], '{:.1f}')} | {rate} | {ram} | {r.failures} |")


def render(results: list[Result], photos: list[int]) -> str:
    lines = ["# Benchmark didascalie", "", f"{len(photos)} foto (id: {', '.join(map(str, photos))}).", "",
             "*s/foto* è il ritmo reale (tempo totale diviso per le foto): con più richieste insieme è più basso della "
             "durata di una richiesta. Prompt e risposta sono mediane per richiesta, dai `timings` di llama-server; "
             "tra parentesi i token del prompt riusati dalla cache.", "",
             "| Modello | Lato | Paralleli | Avvio | s/foto | s/richiesta | Prompt tok (cache) | Prompt s | "
             "Risposta tok | Risposta s | tok/s | RAM di picco | Errori |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    lines += [_row(r) for r in results]
    lines += ["", "## Testi, foto per foto", ""]
    for pid in photos:
        lines.append(f"### Foto {pid}")
        for r in results:
            if (answer := r.answers.get(pid)) is not None:
                lines.append(f"- **{r.label}** ({answer.seconds:.1f} s): {answer.text}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=15, help="numero di foto (predefinito 15)")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--sizes", nargs="+", type=int, default=list(DEFAULT_SIZES), help="lato lungo dell'immagine in px")
    parser.add_argument("--parallel", nargs="+", type=int, default=[1], help="richieste insieme (predefinito 1)")
    parser.add_argument("--server-dir", help="cartella di un'altra build di llama.cpp (per esempio con Vulkan)")
    parser.add_argument("--out", default="captions.md", help="nome del file in <data_dir>/bench (predefinito captions.md)")
    args = parser.parse_args()

    ctx = Context.open(config_mod.load())
    try:
        photos = pick_photos(ctx, args.count)
        if not photos:
            print("nessuna foto elaborata: esegui prima `python -m mosaic index`")
            return 1
        results = [r for model in args.models for size in args.sizes for parallel in args.parallel
                   if (r := run_variant(ctx, model, size, parallel, photos, args.server_dir)) is not None]
        out = ctx.data_dir / "bench" / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(results, photos), encoding="utf-8")
        print(f"\nRisultati in {out}")
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
