"""Cross-platform behaviour: paths, process detaching and audio fallbacks.

Everything here is exercised on whatever OS the suite happens to run on, so
the platform-dependent branches are written as pure functions of `os.name`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import config
import peon_event
import peon_play
import pytest


# ------------------------------ directories -------------------------------


def test_windows_uses_appdata_not_xdg():
    home = Path("C:/Users/peon")
    env = {"APPDATA": "C:/Users/peon/AppData/Roaming",
           "LOCALAPPDATA": "C:/Users/peon/AppData/Local",
           "XDG_CONFIG_HOME": "/should/be/ignored"}
    cfg, state, data = config.platform_dirs(env, home, osname="nt")
    assert cfg == Path("C:/Users/peon/AppData/Roaming/peon-py")
    assert state == Path("C:/Users/peon/AppData/Local/peon-py/state")
    assert data == Path("C:/Users/peon/AppData/Local/peon-py")


def test_windows_falls_back_to_the_profile_when_appdata_is_unset():
    cfg, _, data = config.platform_dirs({}, Path("C:/Users/peon"), osname="nt")
    assert cfg == Path("C:/Users/peon/AppData/Roaming/peon-py")
    assert data == Path("C:/Users/peon/AppData/Local/peon-py")


def test_posix_honours_xdg():
    env = {"XDG_CONFIG_HOME": "/x/config", "XDG_STATE_HOME": "/x/state"}
    cfg, state, data = config.platform_dirs(env, Path("/home/peon"), osname="posix")
    assert cfg == Path("/x/config/peon-py")
    assert state == Path("/x/state/peon-py")
    assert data == Path("/home/peon/.local/share/peon-py")


def test_posix_defaults_without_xdg():
    cfg, state, data = config.platform_dirs({}, Path("/home/peon"), osname="posix")
    assert cfg == Path("/home/peon/.config/peon-py")
    assert state == Path("/home/peon/.local/state/peon-py")
    assert data == Path("/home/peon/.local/share/peon-py")


def test_packs_live_where_the_spec_says_on_every_platform():
    # Spec 8.2 fixes this location; it is not an XDG/AppData directory.
    assert config.GLOBAL_PACKS_DIR.name == "packs"
    assert config.GLOBAL_PACKS_DIR.parent.name == ".openpeon"


# --------------------------- detaching the player -------------------------


def test_posix_players_get_their_own_session(monkeypatch):
    monkeypatch.setattr(peon_event.os, "name", "posix")
    kwargs = peon_event.detached_kwargs()
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_windows_players_get_detached_creation_flags(monkeypatch):
    monkeypatch.setattr(peon_event.os, "name", "nt")
    kwargs = peon_event.detached_kwargs()
    assert "start_new_session" not in kwargs  # POSIX-only, silently ignored
    flags = kwargs["creationflags"]
    assert flags & getattr(subprocess, "DETACHED_PROCESS", 0x8)
    assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)


def test_the_player_interpreter_is_unchanged_off_windows(monkeypatch):
    monkeypatch.setattr(peon_event.os, "name", "posix")
    assert peon_event._windowless("/usr/bin/python3") == "/usr/bin/python3"


def test_windows_prefers_pythonw_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(peon_event.os, "name", "nt")
    (tmp_path / "pythonw.exe").write_text("")
    exe = os.path.join(str(tmp_path), "python.exe")
    assert peon_event._windowless(exe) == os.path.join(str(tmp_path), "pythonw.exe")


def test_windows_keeps_the_console_interpreter_when_pythonw_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(peon_event.os, "name", "nt")
    exe = os.path.join(str(tmp_path), "python.exe")
    assert peon_event._windowless(exe) == exe


def test_windows_reports_winsound_when_no_system_binary_is_installed(monkeypatch):
    monkeypatch.setattr(peon_play.os, "name", "nt")
    monkeypatch.setattr(peon_play.shutil, "which", lambda name: None)
    assert peon_play.system_player_name() == "winsound/powershell"


# ------------------------------- hook argv --------------------------------


def test_the_agent_arrives_as_a_flag_not_an_env_prefix(monkeypatch):
    monkeypatch.delenv("PEON_AGENT", raising=False)
    args, agent = peon_event.split_agent(["Stop", "--agent", "claude"])
    assert (args, agent) == (["Stop"], "claude")


def test_the_agent_flag_also_accepts_equals_form(monkeypatch):
    monkeypatch.delenv("PEON_AGENT", raising=False)
    assert peon_event.split_agent(["Stop", "--agent=codex"]) == (["Stop"], "codex")


def test_the_agent_env_var_still_works(monkeypatch):
    monkeypatch.setenv("PEON_AGENT", "codex")
    assert peon_event.split_agent(["Stop"]) == (["Stop"], "codex")


def test_subcommand_arguments_survive_the_split(monkeypatch):
    monkeypatch.delenv("PEON_AGENT", raising=False)
    assert peon_event.split_agent(["install", "peon"]) == (["install", "peon"], "")


# ------------------------------ audio backends ----------------------------


def test_no_window_flags_are_windows_only():
    if peon_play.os.name == "nt":
        assert peon_play.NO_WINDOW["creationflags"]
    else:
        assert peon_play.NO_WINDOW == {}


def test_a_missing_file_is_never_played(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(peon_play.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(peon_play.sys, "argv", ["peon_play.py", str(tmp_path / "gone.wav")])
    assert peon_play.main() == 0
    assert called == []


def test_the_powershell_fallback_passes_the_path_through_the_environment(monkeypatch, tmp_path):
    # A filename with a quote must not be able to break out of the script.
    path = tmp_path / "it's a sound.mp3"
    path.write_bytes(b"ID3")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env", {})
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(peon_play.shutil, "which", lambda b: "C:/pwsh.exe" if b == "pwsh" else None)
    monkeypatch.setattr(peon_play.subprocess, "run", fake_run)
    assert peon_play._play_windows(path, 0.4) is True
    assert seen["env"]["PEON_PLAY_FILE"] == str(path)
    assert seen["env"]["PEON_PLAY_VOLUME"] == "0.400"
    assert str(path) not in " ".join(seen["cmd"])


def test_windows_plays_wav_through_stdlib_winsound(monkeypatch, tmp_path):
    path = tmp_path / "ready.wav"
    path.write_bytes(b"RIFF")
    played = []
    fake = type("winsound", (), {"SND_FILENAME": 0x20000,
                                 "PlaySound": staticmethod(lambda p, f: played.append(p))})
    monkeypatch.setitem(__import__("sys").modules, "winsound", fake)
    monkeypatch.setattr(peon_play.subprocess, "run", lambda *a, **k: pytest.fail("used PowerShell"))
    assert peon_play._play_windows(path, 0.5) is True
    assert played == [str(path)]


@pytest.mark.parametrize("binary", ["pw-play", "paplay", "ffplay", "mpv", "play", "afplay", "aplay"])
def test_every_system_player_builds_a_command(binary):
    build = dict(peon_play.SYSTEM_PLAYERS)[binary]
    argv = build("/tmp/a.wav", 0.5)
    assert argv[0] == binary and "/tmp/a.wav" in argv
