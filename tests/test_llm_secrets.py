import pytest

from mosaic.llm import secrets


def test_key_roundtrip_and_removal(tmp_path):
    assert secrets.get_key(tmp_path, "nvidia") is None
    secrets.set_key(tmp_path, "nvidia", "  nvapi-abc \n")
    assert secrets.get_key(tmp_path, "nvidia") == "nvapi-abc"
    assert secrets.key_status(tmp_path) == {"nvidia": "file", "openrouter": None}
    secrets.set_key(tmp_path, "nvidia", "")
    assert secrets.get_key(tmp_path, "nvidia") is None


def test_saved_key_wins_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "dall-ambiente")
    assert secrets.get_key(tmp_path, "nvidia") == "dall-ambiente"
    assert secrets.key_status(tmp_path)["nvidia"] == "env"
    secrets.set_key(tmp_path, "nvidia", "salvata")
    assert secrets.get_key(tmp_path, "nvidia") == "salvata"
    assert secrets.key_status(tmp_path)["nvidia"] == "file"


def test_other_providers_are_untouched(tmp_path):
    secrets.set_key(tmp_path, "nvidia", "a")
    secrets.set_key(tmp_path, "openrouter", "b")
    secrets.set_key(tmp_path, "nvidia", None)
    assert secrets.get_key(tmp_path, "openrouter") == "b"


@pytest.mark.parametrize("provider,key", [("boh", "x"), ("nvidia", "a" * 600), ("nvidia", "due\nrighe")])
def test_invalid_input_is_rejected(tmp_path, provider, key):
    with pytest.raises(ValueError):
        secrets.set_key(tmp_path, provider, key)


def test_broken_file_reads_as_no_keys(tmp_path):
    (tmp_path / secrets.FILE_NAME).write_text(": : [", encoding="utf-8")
    assert secrets.get_key(tmp_path, "nvidia") is None
