from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mosaic import desktop


def test_edge_app_args_use_app_mode_and_dedicated_profile(tmp_path: Path) -> None:
    args = desktop.edge_app_args(Path("C:/Edge/msedge.exe"), "http://127.0.0.1:8000/", tmp_path)
    assert args[0].endswith("msedge.exe")
    assert "--app=http://127.0.0.1:8000/" in args
    assert f"--user-data-dir={tmp_path}" in args
    assert "--window-size=1440,920" in args


def test_edge_executable_found_in_program_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert desktop.edge_executable() == edge


def test_edge_executable_missing_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    assert desktop.edge_executable() is None


def test_wait_until_ready_true_when_port_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter([False, False, True])
    monkeypatch.setattr(desktop, "is_running", lambda _port: next(calls))
    assert desktop.wait_until_ready(8000, timeout=5, poll=0)


def test_wait_until_ready_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "is_running", lambda _port: False)
    assert not desktop.wait_until_ready(8000, timeout=0.05, poll=0.01)


def test_show_ui_prefers_native_window(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []
    monkeypatch.setattr(desktop, "open_webview", lambda _url: used.append("webview") or True)
    monkeypatch.setattr(desktop, "open_edge_app", lambda _url: used.append("edge") or True)
    desktop.show_ui("http://x/", keep_alive=None)
    assert used == ["webview"]


def test_show_ui_falls_back_to_edge_then_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []
    monkeypatch.setattr(desktop, "open_webview", lambda _url: False)
    monkeypatch.setattr(desktop, "open_edge_app", lambda _url: used.append("edge") or False)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: used.append(f"browser:{url}"))
    done = threading.Thread(target=lambda: None)
    done.start()
    desktop.show_ui("http://x/", keep_alive=done)
    assert used == ["edge", "browser:http://x/"]


def test_run_reuses_running_server(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "is_running", lambda _port: True)
    monkeypatch.setattr(desktop, "_start_server", lambda _port: pytest.fail("non deve avviare un secondo server"))
    monkeypatch.setattr(desktop, "show_ui", lambda url, keep_alive: opened.append(url))
    assert desktop.run(port=8123) == 0
    assert opened == ["http://127.0.0.1:8123/"]


def test_run_stops_server_when_window_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeServer:
        should_exit = False

    server = FakeServer()
    thread = threading.Thread(target=lambda: None)
    thread.start()
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "is_running", lambda _port: False)
    monkeypatch.setattr(desktop, "_start_server", lambda _port: (server, thread))
    monkeypatch.setattr(desktop, "wait_until_ready", lambda _port: True)
    monkeypatch.setattr(desktop, "show_ui", lambda url, keep_alive: None)
    assert desktop.run(port=8124) == 0
    assert server.should_exit


def test_run_reports_server_that_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeServer:
        should_exit = False

    server = FakeServer()
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "is_running", lambda _port: False)
    monkeypatch.setattr(desktop, "_start_server", lambda _port: (server, threading.Thread(target=lambda: None)))
    monkeypatch.setattr(desktop, "wait_until_ready", lambda _port: False)
    monkeypatch.setattr(desktop, "_fatal", lambda message: 1)
    assert desktop.run(port=8125) == 1
    assert server.should_exit
