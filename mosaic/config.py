"""Configurazione utente (config.yaml): nessun percorso hardcoded, valori validati all'ingresso."""

from __future__ import annotations

import os
from dataclasses import MISSING, dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from mosaic.paths import DEFAULT_DATA_DIR, ROOT

CLOUD_PROVIDERS = ("nvidia", "openrouter")  # servizi cloud per le didascalie, nell'ordine predefinito di tentativo
PLACEHOLDERS = {"seq", "slug", "date", "year", "month", "day", "label", "title", "name"}


class ConfigError(ValueError):
    """Valore di configurazione non valido."""


@dataclass(frozen=True)
class ThumbsConfig:
    pregenerate: tuple[str, ...] = (
        "tile_50", "tile_100", "tile_224", "tile_500", "fit_720", "fit_1280", "fit_1920",
    )
    quality: int = 83


@dataclass(frozen=True)
class AiConfig:
    enabled: bool = True
    model: str = "siglip2-base-224"
    threads: int = 0
    provider: str = "cpu"  # cpu | dml
    detect_nsfw: bool = False
    faces: bool = True
    label_threshold: float = 0.35
    max_labels: int = 5
    batch_size: int = 8
    near_duplicate_hamming: int = 6
    near_duplicate_cosine: float = 0.97


@dataclass(frozen=True)
class CloudConfig:
    """Didascalie su un servizio cloud gratuito, più veloce del PC: spento di default (le foto escono dal computer).

    Le chiavi API non stanno qui ma in data/secrets.yaml (mosaic.llm.secrets).
    """
    enabled: bool = False
    providers: tuple[str, ...] = CLOUD_PROVIDERS  # ordine di tentativo; il modello locale è sempre l'ultimo ripiego
    nvidia_model: str = "google/gemma-4-31b-it"
    openrouter_model: str = "google/gemma-4-31b-it:free"
    parallel: int = 4          # richieste insieme verso il cloud
    send_names: bool = True    # nomi delle persone confermate nel prompt


@dataclass(frozen=True)
class LlmConfig:
    mode: str = "on-demand"    # newly-indexed | on-demand | never; fuori dalla pipeline finché non si sceglie newly-indexed
    model: str = "qwen3vl-2b"     # 1,5 GB: su 8 GB resta tutto in RAM (il 3B veniva paginato, 4-5 volte più lento)
    idle_seconds: int = 300
    threads: int = 0           # 0 = core fisici
    context: int = 2048        # per richiesta: una richiesta (immagine + prompt + risposta) resta sotto i 600 token
    parallel: int = 2          # richieste insieme a llama-server, uno slot ciascuna: su CPU generare 2 risposte costa poco più di 1
    language: str = "it"
    image_size: int = 448      # lato lungo dell'immagine data all'LLM: meno pixel, meno token visivi
    about: str = ""            # chi sei e cosa fotografi: entra nel contesto di ogni didascalia
    caption: bool = False      # anche la frase di didascalia: è circa metà dei token generati (e del tempo); spenta = titolo e parole chiave
    cloud: CloudConfig = field(default_factory=CloudConfig)


@dataclass(frozen=True)
class RenameConfig:
    auto: bool = True
    pattern: str = "{seq:04}_{slug}"
    slug_max: int = 60
    seq_start: int = 1


@dataclass(frozen=True)
class Config:
    library_roots: tuple[str, ...] = ()
    data_dir: str = str(DEFAULT_DATA_DIR)
    import_dir: str = ""
    workers: int = 0
    port: int = 8000
    stack_sequences: bool = False
    stack_uuid: bool = True    # unisce le foto con lo stesso DocumentID XMP
    stack_meta: bool = False   # unisce le foto con stessa data, GPS e seriale (rischioso con le raffiche)
    auto_index_delay: int = 0
    delete_mode: str = "catalog"  # cosa fa «Elimina» su una foto: catalog (solo dal catalogo) | trash (file nel Cestino)
    thumbs: ThumbsConfig = field(default_factory=ThumbsConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    rename: RenameConfig = field(default_factory=RenameConfig)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    def worker_count(self) -> int:
        """Numero di worker: auto = 2-3 su PC con poca RAM, mai più della metà dei core."""
        if self.workers > 0:
            return self.workers
        cores = os.cpu_count() or 2
        return max(1, min(3, cores // 2))


def _build[T](cls: type[T], data: dict[str, Any]) -> T:
    """Costruisce una dataclass annidata ignorando le chiavi sconosciute e convertendo le liste in tuple."""
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        value = data[f.name]
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default) and isinstance(value, dict):
            value = _build(type(default), value)
        elif isinstance(default, tuple) and isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def validate(cfg: Config) -> Config:
    if not 1 <= cfg.port <= 65535:
        raise ConfigError(f"port non valida: {cfg.port}")
    if cfg.workers < 0:
        raise ConfigError("workers deve essere >= 0")
    if cfg.delete_mode not in {"catalog", "trash"}:
        raise ConfigError(f"delete_mode non valido: {cfg.delete_mode}")
    if cfg.llm.mode not in {"newly-indexed", "on-demand", "never"}:
        raise ConfigError(f"llm.mode non valido: {cfg.llm.mode}")
    if len(cfg.llm.about) > 2000:
        raise ConfigError("llm.about è troppo lungo (massimo 2000 caratteri)")
    if not 224 <= cfg.llm.image_size <= 1024:
        raise ConfigError("llm.image_size deve stare tra 224 e 1024")
    if not 1 <= cfg.llm.parallel <= 4:
        raise ConfigError("llm.parallel deve stare tra 1 e 4")
    check_cloud(cfg.llm.cloud)
    if cfg.ai.provider not in {"cpu", "dml"}:
        raise ConfigError(f"ai.provider non valido: {cfg.ai.provider}")
    if not 0 <= cfg.ai.label_threshold <= 1:
        raise ConfigError("ai.label_threshold deve stare tra 0 e 1")
    if not 1 <= cfg.thumbs.quality <= 100:
        raise ConfigError("thumbs.quality deve stare tra 1 e 100")
    check_pattern(cfg.rename.pattern)
    return cfg


def check_cloud(cloud: CloudConfig) -> None:
    if len(set(cloud.providers)) != len(cloud.providers) or not set(cloud.providers) <= set(CLOUD_PROVIDERS):
        raise ConfigError(f"llm.cloud.providers deve contenere servizi diversi tra {list(CLOUD_PROVIDERS)}")
    if not 1 <= cloud.parallel <= 8:
        raise ConfigError("llm.cloud.parallel deve stare tra 1 e 8")
    if not cloud.nvidia_model.strip() or not cloud.openrouter_model.strip():
        raise ConfigError("i modelli cloud non possono essere vuoti")


def check_pattern(pattern: str) -> None:
    """Lo schema di rinomina può contenere solo segnaposto noti."""
    import string

    try:
        names = {name for _, name, _, _ in string.Formatter().parse(pattern) if name}
    except ValueError as exc:
        raise ConfigError(f"schema di rinomina non valido: {exc}") from exc
    unknown = names - PLACEHOLDERS
    if unknown:
        raise ConfigError(f"segnaposto sconosciuti nello schema: {sorted(unknown)}")
    if "seq" not in names and "slug" not in names and "date" not in names:
        raise ConfigError("lo schema deve contenere almeno {seq}, {slug} o {date}")


def config_path() -> Path:
    return Path(os.environ.get("MOSAIC_CONFIG", ROOT / "config.yaml"))


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return validate(Config())
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: il file deve contenere una mappa YAML")
    return validate(_build(Config, raw))


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_to_plain(v) for v in value]
    return value


def to_dict(cfg: Config) -> dict[str, Any]:
    return _to_plain(cfg)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(to_dict(validate(cfg)), sort_keys=False, allow_unicode=True), encoding="utf-8")


def update(cfg: Config, changes: dict[str, Any]) -> Config:
    """Applica modifiche parziali (anche annidate) e restituisce una nuova configurazione validata."""
    merged = _merge(to_dict(cfg), changes)
    return validate(_build(Config, merged))


def _merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in changes.items():
        if key not in base:
            continue
        out[key] = _merge(base[key], value) if isinstance(base[key], dict) and isinstance(value, dict) else value
    return out


__all__ = ["Config", "ConfigError", "load", "replace", "save", "to_dict", "update", "validate"]
