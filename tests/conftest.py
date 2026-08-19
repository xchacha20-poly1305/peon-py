"""Shared fixtures for the peon-py test suite.

`scripts/config.py` resolves its paths at import time, so tests rebind the
module attributes rather than juggling XDG environment variables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import config  # noqa: E402


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Redirect every config/state/pack path into a temp tree.

    Yields the tmp root; `paths / "packs"` is the global pack directory and
    `paths / "config" / "config.json"` the user config.
    """
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    data_dir = tmp_path / "data"
    packs_dir = tmp_path / "packs"
    project = tmp_path / "project"
    for d in (config_dir, state_dir, data_dir, packs_dir, project):
        d.mkdir()

    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config, "LOG_PATH", state_dir / "event.log")
    monkeypatch.setattr(config, "INSTALL_LOG_PATH", state_dir / "install.log")
    monkeypatch.setattr(config, "PLAY_STATE_PATH", state_dir / "play-state.json")
    monkeypatch.setattr(config, "VENV_DIR", data_dir / "venv")
    monkeypatch.setattr(config, "VENV_BIN", data_dir / "venv" / "bin")
    monkeypatch.setattr(config, "VENV_PY", data_dir / "venv" / "bin" / "python")
    monkeypatch.setattr(config, "VENV_STATE_PATH", data_dir / "venv" / ".peon-runtime.json")
    monkeypatch.setattr(config, "GLOBAL_PACKS_DIR", packs_dir)
    monkeypatch.chdir(project)
    return tmp_path


def write_config(root: Path, cfg: dict) -> None:
    (root / "config" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


WAV_HEADER = b"RIFF\x24\x00\x00\x00WAVEfmt "


def make_pack(root: Path, name: str, categories: dict[str, list[tuple[str, str]]],
              *, aliases: dict | None = None, create_files: bool = True,
              cesp_version: str = "1.0") -> Path:
    """Create a minimal CESP pack.

    `categories` maps a category name to a list of (filename, label) pairs.
    """
    pack_dir = root / name
    (pack_dir / "sounds").mkdir(parents=True, exist_ok=True)
    manifest = {
        "cesp_version": cesp_version,
        "name": name,
        "display_name": name.title(),
        "version": "1.0.0",
        "categories": {
            cat: {"sounds": [{"file": f"sounds/{f}", "label": label} for f, label in items]}
            for cat, items in categories.items()
        },
    }
    if aliases:
        manifest["category_aliases"] = aliases
    (pack_dir / "openpeon.json").write_text(json.dumps(manifest), encoding="utf-8")
    if create_files:
        for items in categories.values():
            for filename, _ in items:
                (pack_dir / "sounds" / filename).write_bytes(WAV_HEADER)
    return pack_dir
