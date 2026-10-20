"""Download e verifica degli asset locali (modelli ONNX/GGUF, exiftool, ffmpeg, llama.cpp).

Uso:  python -m mosaic.assets fetch [--group core|llm|llm-extra|all]
      python -m mosaic.assets status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx
import yaml

from mosaic.paths import ROOT

MANIFEST = ROOT / "assets.yaml"
LOCK = ROOT / "models" / "assets.lock.json"
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, expected_sha: str | None) -> str:
    """Scarica con ripresa (Range) e verifica lo SHA-256 se noto."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    if target.exists() and expected_sha and sha256_file(target) == expected_sha:
        return expected_sha
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as resp:
        if resp.status_code == 200:
            offset = 0
        elif resp.status_code != 206:
            resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0)) + offset
        mode = "ab" if offset else "wb"
        done = offset
        with part.open(mode) as fh:
            for block in resp.iter_bytes(CHUNK):
                fh.write(block)
                done += len(block)
                if total:
                    print(f"\r  {target.name}: {done / 1e6:8.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    actual = sha256_file(part)
    if expected_sha and actual != expected_sha:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 non valido per {target.name}: atteso {expected_sha}, ottenuto {actual}")
    part.replace(target)
    return actual


def _hf_files(spec: dict) -> list[tuple[str, str, str | None]]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(spec["repo"], files_metadata=True)
    lfs = {s.rfilename: (s.lfs.sha256 if s.lfs else None) for s in info.siblings}
    out = []
    for name in spec["files"]:
        if name not in lfs:
            raise RuntimeError(f"{name} non trovato in {spec['repo']}")
        url = f"https://huggingface.co/{spec['repo']}/resolve/{info.sha}/{name}"
        out.append((name, url, lfs[name]))
    return out


def _github_asset(spec: dict) -> tuple[str, str]:
    api = f"https://api.github.com/repos/{spec['repo']}/releases/tags/{spec['tag']}"
    data = httpx.get(api, timeout=30, follow_redirects=True).raise_for_status().json()
    for asset in data["assets"]:
        if asset["name"] == spec["asset"]:
            digest = (asset.get("digest") or "").removeprefix("sha256:")
            return asset["browser_download_url"], digest
    raise RuntimeError(f"asset {spec['asset']} non trovato in {spec['repo']}@{spec['tag']}")


def _extract(archive: Path, dest: Path, spec: dict) -> None:
    only = spec.get("only")
    rename = spec.get("rename", {})
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        root = ""
        if spec.get("strip_root") or only:
            first = {n.split("/", 1)[0] for n in names}
            root = (first.pop() + "/") if len(first) == 1 else ""
        for name in names:
            rel = name[len(root):] if name.startswith(root) else name
            if only and rel not in only:
                continue
            rel_path = Path(rel).name if only else Path(rel)
            rel_path = Path(rename.get(str(rel_path), str(rel_path)))
            out = (dest / rel_path).resolve()
            if dest.resolve() not in out.parents and out != dest.resolve():
                raise RuntimeError(f"percorso non sicuro nell'archivio: {name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def fetch_asset(key: str, spec: dict, lock: dict) -> None:
    dest = ROOT / spec["dest"]
    print(f"[{key}] -> {dest}")
    kind = spec["kind"]
    if kind == "huggingface":
        for name, url, sha in _hf_files(spec):
            target = dest / Path(name).name
            lock[f"{key}/{name}"] = download(url, target, sha)
        return
    if kind == "github":
        url, sha = _github_asset(spec)
    else:
        url, sha = spec["url"], spec.get("sha256")
    marker = dest / ".installed"
    if marker.exists() and marker.read_text().strip() == (sha or url):
        print("  già installato")
        return
    with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as tmp:
        archive = Path(tmp) / "pkg.zip"
        lock[key] = download(url, archive, sha or None)
        if dest.exists():
            shutil.rmtree(dest)
        _extract(archive, dest, spec)
    marker.write_text(sha or url)


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["assets"]


def fetch(group: str) -> int:
    (ROOT / "runtime").mkdir(exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    failures = []
    for key, spec in load_manifest().items():
        if group != "all" and spec.get("group") != group:
            continue
        try:
            fetch_asset(key, spec, lock)
        except Exception as exc:  # un asset fallito non blocca gli altri
            failures.append(key)
            print(f"  ERRORE {key}: {exc}", file=sys.stderr)
        LOCK.write_text(json.dumps(lock, indent=2))
    print("Falliti: " + ", ".join(failures) if failures else "Tutti gli asset sono pronti.")
    return 1 if failures else 0


def status() -> dict[str, bool]:
    result = {}
    for key, spec in load_manifest().items():
        dest = ROOT / spec["dest"]
        if spec["kind"] == "huggingface":
            result[key] = all((dest / Path(f).name).exists() for f in spec["files"])
        else:
            result[key] = (dest / ".installed").exists()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mosaic.assets")
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--group", default="all", choices=["core", "llm", "llm-extra", "all"])
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        return fetch(args.group)
    for key, ok in status().items():
        print(f"{'OK ' if ok else '-- '} {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
