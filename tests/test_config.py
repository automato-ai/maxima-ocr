import os
import textwrap

import pytest

import config


def test_read_config_loads_yaml_from_cwd(tmp_path, monkeypatch):
    yaml_text = textwrap.dedent("""\
        modbus_server:
          accept: 127.0.0.1
          port: 1502
        capture:
          folder: ./capture
          frames: 5
        camera:
          format: ANY
        logging:
          version: 1
    """)
    (tmp_path / "config.yaml").write_text(yaml_text)
    monkeypatch.chdir(tmp_path)

    cfg = config.read_config()

    assert cfg["modbus_server"]["accept"] == "127.0.0.1"
    assert cfg["modbus_server"]["port"] == 1502
    assert cfg["capture"]["frames"] == 5
    assert cfg["camera"]["format"] == "ANY"


def test_read_config_returns_top_level_keys(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("modbus_server: {}\ncapture: {}\ncamera: {}\nlogging: {}\n")
    monkeypatch.chdir(tmp_path)

    cfg = config.read_config()

    for key in ("modbus_server", "capture", "camera", "logging"):
        assert key in cfg


def test_read_config_raises_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        config.read_config()


def test_read_config_uses_current_working_directory(tmp_path, monkeypatch):
    """Pinning CLAUDE.md's note that config is read from os.getcwd()."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "config.yaml").write_text("camera:\n  format: V4L2\n")
    (tmp_path / "config.yaml").write_text("camera:\n  format: DSHOW\n")

    monkeypatch.chdir(sub)
    assert config.read_config()["camera"]["format"] == "V4L2"

    monkeypatch.chdir(tmp_path)
    assert config.read_config()["camera"]["format"] == "DSHOW"
