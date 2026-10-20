from pathlib import Path

import pytest

from mosaic import config as cfgmod
from mosaic.config import Config, ConfigError


def test_defaults_are_valid():
    assert cfgmod.validate(Config()).port == 8000


def test_load_missing_file_returns_defaults(tmp_path: Path):
    assert cfgmod.load(tmp_path / "nope.yaml").llm.mode == "on-demand"


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "config.yaml"
    cfg = cfgmod.update(Config(), {"library_roots": ["D:/Foto"], "rename": {"pattern": "{date:%Y}_{slug}"}})
    cfgmod.save(cfg, path)
    loaded = cfgmod.load(path)
    assert loaded.library_roots == ("D:/Foto",)
    assert loaded.rename.pattern == "{date:%Y}_{slug}"
    assert loaded.thumbs.pregenerate == Config().thumbs.pregenerate


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("port: 9000\nnonsense: 1\nai:\n  zzz: 2\n", encoding="utf-8")
    assert cfgmod.load(path).port == 9000


@pytest.mark.parametrize(
    "changes",
    [{"port": 0}, {"llm": {"mode": "boh"}}, {"ai": {"provider": "gpu"}}, {"rename": {"pattern": "{nope}"}},
     {"rename": {"pattern": "fisso"}}, {"ai": {"label_threshold": 3}}, {"workers": -1}, {"llm": {"image_size": 64}},
     {"llm": {"parallel": 0}}, {"llm": {"parallel": 5}},
     {"llm": {"cloud": {"providers": ["nvidia", "boh"]}}}, {"llm": {"cloud": {"providers": ["nvidia", "nvidia"]}}},
     {"llm": {"cloud": {"parallel": 0}}}, {"llm": {"cloud": {"parallel": 9}}}, {"llm": {"cloud": {"nvidia_model": " "}}}],
)
def test_invalid_values_are_rejected(changes):
    with pytest.raises(ConfigError):
        cfgmod.update(Config(), changes)


def test_worker_count_is_bounded():
    assert 1 <= Config().worker_count() <= 3
    assert Config(workers=5).worker_count() == 5


def test_cloud_is_off_by_default_and_roundtrips(tmp_path: Path):
    assert Config().llm.cloud.enabled is False
    assert Config().llm.cloud.providers == ("nvidia", "openrouter")
    path = tmp_path / "config.yaml"
    cfgmod.save(cfgmod.update(Config(), {"llm": {"cloud": {"enabled": True, "providers": ["openrouter"]}}}), path)
    loaded = cfgmod.load(path).llm.cloud
    assert loaded.enabled and loaded.providers == ("openrouter",)
    assert "key" not in path.read_text(encoding="utf-8").lower()


def test_migrations_are_idempotent_and_split_statements(tmp_path):
    from mosaic.db.connection import MIGRATIONS, Database, _statements

    assert list(_statements("-- nota\nCREATE TABLE a (x TEXT DEFAULT ';');\n\nCREATE INDEX i ON a(x);\n")) == [
        "CREATE TABLE a (x TEXT DEFAULT ';');\n", "\nCREATE INDEX i ON a(x);\n"]
    first = Database(tmp_path / "m.db")
    second = Database(tmp_path / "m.db")  # un secondo Mosaic che parte subito dopo: niente «already exists»
    assert first.migrate() == second.migrate() == MIGRATIONS[-1][0]
