"""The detached player: backend choice and volume scaling."""
from __future__ import annotations

import peon_play
import pytest


@pytest.fixture()
def only(monkeypatch):
    """Pretend exactly one system player binary is installed."""
    def factory(binary: str | None):
        monkeypatch.setattr(peon_play.shutil, "which",
                            lambda name: f"/usr/bin/{name}" if name == binary else None)
    return factory


def build(binary: str, volume: float) -> list[str]:
    for name, builder in peon_play.SYSTEM_PLAYERS:
        if name == binary:
            return builder("/tmp/a.wav", volume)
    raise AssertionError(f"{binary} is not a known backend")


def test_pipewire_is_preferred(only):
    only("pw-play")
    assert peon_play.system_player_name() == "pw-play"


def test_no_backend_reports_empty(only, monkeypatch):
    only(None)
    monkeypatch.setattr(peon_play.os, "name", "posix")
    assert peon_play.system_player_name() == ""


@pytest.mark.parametrize("binary,volume,expected", [
    ("pw-play", 0.5, "--volume=0.500"),
    ("paplay", 0.5, "--volume=32768"),
    ("ffplay", 0.5, "50"),
    ("mpv", 0.25, "--volume=25"),
    ("play", 0.25, "0.250"),
    ("afplay", 1.0, "1.000"),
])
def test_volume_is_scaled_into_each_backend_range(binary, volume, expected):
    assert expected in build(binary, volume)


def test_every_backend_receives_the_file_path():
    for name, builder in peon_play.SYSTEM_PLAYERS:
        assert "/tmp/a.wav" in builder("/tmp/a.wav", 0.5), name


def test_a_missing_file_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(tmp_path / "nope.wav")])
    calls = []
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: calls.append(p) or True)
    assert peon_play.main() == 0
    assert calls == []


def test_the_system_player_is_used_when_the_python_stack_is_unavailable(monkeypatch, tmp_path):
    sound = tmp_path / "a.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(sound), "0.3"])
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: False)
    used = []
    monkeypatch.setattr(peon_play, "play_with_system", lambda p, v: used.append((p, v)) or True)
    peon_play.main()
    assert used == [(sound, pytest.approx(0.3))]


def test_a_bad_volume_argument_falls_back_to_the_default(monkeypatch, tmp_path):
    sound = tmp_path / "a.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(sound), "loud"])
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: True)
    seen = []
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: seen.append(v) or True)
    peon_play.main()
    assert seen == [0.5]


# --- spec 4.4: the player rejects a file that is not what it claims ---

def test_main_refuses_to_play_a_fake_wav(tmp_path, monkeypatch):
    fake = tmp_path / "Hello.wav"
    fake.write_bytes(b"#!/bin/sh\nrm -rf /\n")
    played: list = []
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: played.append(p) or True)
    monkeypatch.setattr(peon_play, "play_with_system", lambda p, v: played.append(p) or True)
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(fake), "0.5"])
    assert peon_play.main() == 0
    assert played == []


def test_main_plays_a_real_wav(tmp_path, monkeypatch):
    good = tmp_path / "Hello.wav"
    good.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    played: list = []
    monkeypatch.setattr(peon_play, "play_with_python", lambda p, v: played.append(p) or True)
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(good), "0.5"])
    assert peon_play.main() == 0
    assert played == [good]
