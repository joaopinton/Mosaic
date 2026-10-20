import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from conftest import make_jpeg

from mosaic import cli
from mosaic import config as cfgmod


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Configurazione isolata: libreria e dati dentro tmp_path, mai il config.yaml vero."""
    library = tmp_path / "lib"
    library.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"library_roots": [str(library)], "data_dir": str(tmp_path / "data"),
                                      "llm": {"mode": "never"}, "ai": {"enabled": False}}), encoding="utf-8")
    monkeypatch.setenv("MOSAIC_CONFIG", str(config))
    return type("Env", (), {"library": library, "config": config, "tmp": tmp_path})


def run(capsys, *args: str) -> tuple[int, str]:
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def db_of(env) -> sqlite3.Connection:
    conn = sqlite3.connect(env.tmp / "data" / "mosaic.db")
    conn.row_factory = sqlite3.Row
    return conn


def test_unknown_command_and_missing_arguments_exit_with_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["comando-inesistente"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        cli.main([])


def test_config_show_and_add_root(env, capsys):
    code, out = run(capsys, "config", "show")
    assert code == 0 and json.loads(out)["llm"]["mode"] == "never"
    extra = env.tmp / "altra"
    extra.mkdir()
    code, out = run(capsys, "config", "add-root", str(extra))
    assert code == 0 and str(extra.resolve()) in out
    assert str(extra.resolve()) in cfgmod.load(env.config).library_roots
    code, out = run(capsys, "config", "add-root", str(env.tmp / "manca"))
    assert code == 2 and "non trovata" in out


def test_index_runs_the_whole_chain_and_renames_then_rollback_restores(env, capsys):
    make_jpeg(env.library / "IMG_20230501_123456.jpg", color=(200, 20, 20), size=(640, 480))
    make_jpeg(env.library / "sub" / "b.jpg", color=(20, 200, 20), size=(640, 480))
    code, out = run(capsys, "index", "--no-ai")
    assert code == 0 and "[index] done" in out and "[rename]" in out
    names = sorted(p.name for p in env.library.rglob("*.jpg"))
    assert all(n[:4].isdigit() and n[4] == "_" for n in names), names     # 0001_..., 0002_...
    conn = db_of(env)
    assert conn.execute("SELECT COUNT(*) FROM photos WHERE meta_at IS NOT NULL").fetchone()[0] == 2
    code, out = run(capsys, "status")
    assert code == 0 and "foto: 2" in out and "asset:" in out
    code, out = run(capsys, "rollback")
    assert code == 2 and "--batch" in out
    code, out = run(capsys, "rollback", "--all")
    assert code == 0 and "ripristinati 2 file" in out
    assert sorted(p.name for p in env.library.rglob("*.jpg")) == ["IMG_20230501_123456.jpg", "b.jpg"]


def test_rename_dry_run_changes_nothing(env, capsys):
    make_jpeg(env.library / "a.jpg", size=(320, 240))
    run(capsys, "index", "--no-ai")
    run(capsys, "rollback", "--all")
    code, out = run(capsys, "rename", "--dry-run", "--pattern", "{date:%Y}_{slug}")
    assert code == 0 and "da rinominare" in out
    assert (env.library / "a.jpg").exists()


def test_search_and_similar_report_clearly(env, capsys):
    make_jpeg(env.library / "a.jpg", size=(320, 240))
    run(capsys, "index", "--no-ai")
    code, out = run(capsys, "search", "label:inesistente")
    assert code == 0 and out.startswith("0 risultati")
    code, out = run(capsys, "search", "order:boh")
    assert code == 2 and "query non valida" in out
    code, out = run(capsys, "search", "year:1999")
    assert code == 0
    code, out = run(capsys, "similar", "zzz-niente")
    assert code == 2 and "nessuna foto" in out
    code, out = run(capsys, "similar", "a")
    assert code == 1 and "embedding" in out


def test_export_import_legacy_cleanup_and_import(env, capsys):
    make_jpeg(env.library / "a.jpg", size=(320, 240))
    run(capsys, "index", "--no-ai")
    out_file = env.tmp / "catalogo" / "c.json"
    code, out = run(capsys, "export", "--format", "json", "--out", str(out_file))
    assert code == 0 and len(json.loads(out_file.read_text(encoding="utf-8"))) == 1
    code, out = run(capsys, "import-legacy", str(env.tmp / "non-esiste.db"))
    assert code == 2 and "non riuscito" in out
    code, out = run(capsys, "cleanup")
    assert code == 0 and "[cleanup] done" in out
    inbox = env.tmp / "inbox"
    make_jpeg(inbox / "nuova.jpg", color=(5, 5, 200), size=(320, 240))
    code, out = run(capsys, "import", str(inbox))
    assert code == 0 and "importate 1" in out
    code, out = run(capsys, "import", str(env.tmp / "manca"))
    assert code == 2 and "impossibile" in out


def test_index_without_library_explains_what_to_do(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"data_dir": str(tmp_path / "data")}), encoding="utf-8")
    monkeypatch.setenv("MOSAIC_CONFIG", str(config))
    code, out = run(capsys, "index")
    assert code == 2 and "nessuna cartella della libreria" in out


def test_index_with_a_path_registers_it_in_the_configuration(env, capsys):
    other = env.tmp / "nuova-libreria"
    make_jpeg(other / "x.jpg", color=(9, 99, 199), size=(320, 240))
    code, out = run(capsys, "index", str(other), "--no-ai")
    assert code == 0 and "aggiunte alla libreria" in out
    assert str(other.resolve()) in cfgmod.load(env.config).library_roots
    assert Path(other / "x.jpg").exists() or any(other.glob("*.jpg"))
