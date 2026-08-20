"""Hook event handling: mapping, mute, debounce, spam detection, dispatch."""
from __future__ import annotations

import io
import json
import sys

import config
import peon_event
import pytest

from .conftest import make_pack, write_config

THREE = {
    "session.start": [("hello.wav", "Ready to work?")],
    "task.complete": [("a.wav", "A"), ("b.wav", "B")],
    "task.error": [("boom.wav", "Ugh.")],
    "user.spam": [("angry.wav", "Whaaat?")],
}


@pytest.fixture()
def played(paths, monkeypatch):
    """Install a pack and capture what would have been played."""
    make_pack(paths / "packs", "peon", THREE)
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(peon_event, "spawn_player",
                        lambda path, volume: calls.append((str(path), volume)))
    # The venv is never built during tests; make sure nothing tries.
    monkeypatch.setattr(peon_event, "venv_ready", lambda: True)
    return calls


def feed_stdin(monkeypatch, payload: dict | str = "") -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# --- event -> category mapping ---

@pytest.mark.parametrize("event,category", [
    ("SessionStart", "session.start"),
    ("SessionEnd", "session.end"),
    ("Stop", "task.complete"),
    ("SubagentStop", "task.complete"),
    ("SubagentStart", "task.acknowledge"),
    ("PreToolUse", "task.progress"),
    ("PostToolUseFailure", "task.error"),
    ("Notification", "input.required"),
    ("PermissionRequest", "input.required"),
    ("PreCompact", "resource.limit"),
])
def test_event_maps_to_the_spec_category(event, category, paths):
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event(event, cfg, state, 1000) == category


def test_unknown_events_map_to_nothing(paths):
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event("SomeFutureHook", cfg, state, 1000) is None


def test_a_lone_prompt_is_an_acknowledge(paths):
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event("UserPromptSubmit", cfg, state, 1000) == "task.acknowledge"


def test_two_fast_prompts_become_spam(paths):
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    peon_event.category_for_event("UserPromptSubmit", cfg, state, 1000)
    assert peon_event.category_for_event("UserPromptSubmit", cfg, state, 1500) == "user.spam"


def test_two_slow_prompts_stay_acknowledge(paths):
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    peon_event.category_for_event("UserPromptSubmit", cfg, state, 1000)
    later = 1000 + cfg["spam_window_ms"] + 1
    assert peon_event.category_for_event("UserPromptSubmit", cfg, state, later) == "task.acknowledge"


def test_event_map_overlay_remaps_a_documented_event(paths):
    write_config(paths, {"event_map": {"Stop": "task.acknowledge"}})
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event("Stop", cfg, state, 1000) == "task.acknowledge"


def test_an_invented_overlay_category_keeps_the_default(paths):
    write_config(paths, {"event_map": {"Stop": "task.explode"}})
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert "Stop" not in cfg["event_map"]
    assert peon_event.category_for_event("Stop", cfg, state, 1000) == "task.complete"


def test_an_unmentioned_event_keeps_the_documented_default(paths):
    write_config(paths, {"event_map": {"Stop": "task.acknowledge"}})
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event("SessionStart", cfg, state, 1000) == "session.start"


def test_rapid_prompts_stay_spam_even_when_acknowledge_is_remapped(paths):
    write_config(paths, {"event_map": {"UserPromptSubmit": "task.complete"}})
    cfg = config.load_user_config()
    state = peon_event.load_play_state()
    assert peon_event.category_for_event("UserPromptSubmit", cfg, state, 1000) == "task.complete"
    assert peon_event.category_for_event("UserPromptSubmit", cfg, state, 1500) == "user.spam"


# --- play_category gating ---

def test_playing_a_category_spawns_the_player(played, paths):
    label = peon_event.play_category("session.start")
    assert label == "Ready to work?"
    assert len(played) == 1
    assert played[0][0].endswith("hello.wav")
    assert played[0][1] == 0.5


def test_mute_silences_everything(played, paths):
    write_config(paths, {"muted": True})
    assert peon_event.play_category("session.start") is None
    assert played == []


def test_a_disabled_category_is_skipped(played, paths):
    write_config(paths, {"categories": {"session.start": False}})
    assert peon_event.play_category("session.start") is None
    assert played == []


def test_task_progress_is_off_by_default(played, paths):
    make_pack(paths / "packs", "peon", dict(THREE, **{"task.progress": [("t.wav", "T")]}))
    assert peon_event.play_category("task.progress") is None
    assert played == []


def test_volume_is_passed_through(played, paths):
    write_config(paths, {"volume": 0.2})
    peon_event.play_category("session.start")
    assert played[0][1] == pytest.approx(0.2)


def test_out_of_range_volume_is_clamped(played, paths):
    write_config(paths, {"volume": 11})
    peon_event.play_category("session.start")
    assert played[0][1] == 1.0


def test_nothing_plays_without_an_installed_pack(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(peon_event, "spawn_player", lambda p, v: calls.append(p))
    monkeypatch.setattr(peon_event, "venv_ready", lambda: True)
    assert peon_event.play_category("session.start") is None
    assert calls == []


def test_a_category_the_pack_lacks_is_a_silent_skip(played, paths):
    assert peon_event.play_category("resource.limit") is None
    assert played == []


# --- debounce (spec 7.3) ---

def test_a_second_event_inside_the_debounce_window_is_dropped(played, paths):
    assert peon_event.play_category("task.complete") is not None
    assert peon_event.play_category("task.complete") is None
    assert len(played) == 1


def test_debounce_is_per_category(played, paths):
    assert peon_event.play_category("task.complete") is not None
    assert peon_event.play_category("session.start") is not None
    assert len(played) == 2


def test_debounce_expires(played, paths, monkeypatch):
    write_config(paths, {"debounce_ms": 50})
    assert peon_event.play_category("task.complete") is not None
    now = peon_event.now_ms()
    monkeypatch.setattr(peon_event, "now_ms", lambda: now + 500)
    assert peon_event.play_category("task.complete") is not None


def test_test_command_bypasses_the_debounce(played, paths):
    peon_event.play_category("task.complete")
    assert peon_event.play_category("task.complete", respect_debounce=False) is not None
    assert len(played) == 2


def test_consecutive_plays_do_not_repeat_the_same_file(played, paths):
    write_config(paths, {"debounce_ms": 0})
    for _ in range(10):
        peon_event.play_category("task.complete")
    files = [p for p, _ in played]
    assert all(a != b for a, b in zip(files, files[1:]))


# --- cmd_event end to end ---

def test_cmd_event_plays_and_survives_hook_json(played, paths, monkeypatch):
    feed_stdin(monkeypatch, {"session_id": "abc", "cwd": str(paths)})
    peon_event.cmd_event("SessionStart")
    assert len(played) == 1


def test_cmd_event_tolerates_garbage_on_stdin(played, paths, monkeypatch):
    feed_stdin(monkeypatch, "not json at all")
    peon_event.cmd_event("SessionStart")
    assert len(played) == 1


def test_cmd_event_ignores_unmapped_events(played, paths, monkeypatch):
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("PreToolUse")
    assert played == []


# --- pack rotation (spec 8.2 MAY) ---

def _two_session_packs(paths) -> None:
    make_pack(paths / "packs", "alpha", {
        "session.start": [("a-start.wav", "A start")],
        "task.complete": [("a-done.wav", "A done")],
    })
    make_pack(paths / "packs", "bravo", {
        "session.start": [("b-start.wav", "B start")],
        "task.complete": [("b-done.wav", "B done")],
    })


def _capture_player(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(peon_event, "spawn_player", lambda p, v: calls.append(str(p)))
    monkeypatch.setattr(peon_event, "venv_ready", lambda: True)
    return calls


def test_round_robin_advances_on_each_session_start(paths, monkeypatch):
    _two_session_packs(paths)
    write_config(paths, {
        "pack": "alpha",
        "pack_rotation": ["alpha", "bravo"],
        "pack_rotation_mode": "round-robin",
        "debounce_ms": 0,
    })
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("SessionStart")
    peon_event.cmd_event("SessionStart")
    assert calls[0].endswith("a-start.wav")
    assert calls[1].endswith("b-start.wav")


def test_a_second_play_in_the_same_session_keeps_the_rotated_pack(paths, monkeypatch):
    _two_session_packs(paths)
    write_config(paths, {
        "pack": "bravo",
        "pack_rotation": ["alpha", "bravo"],
        "pack_rotation_mode": "round-robin",
        "debounce_ms": 0,
    })
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("SessionStart")
    peon_event.play_category("task.complete")
    assert calls[0].endswith("a-start.wav")
    assert calls[1].endswith("a-done.wav")


def test_rotation_skips_names_that_are_not_on_disk(paths, monkeypatch):
    _two_session_packs(paths)
    write_config(paths, {
        "pack": "alpha",
        "pack_rotation": ["ghost", "alpha", "deleted", "bravo"],
        "pack_rotation_mode": "round-robin",
        "debounce_ms": 0,
    })
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("SessionStart")
    peon_event.cmd_event("SessionStart")
    assert calls[0].endswith("a-start.wav")
    assert calls[1].endswith("b-start.wav")


def test_rotation_off_keeps_the_configured_pack(paths, monkeypatch):
    _two_session_packs(paths)
    write_config(paths, {
        "pack": "alpha",
        "pack_rotation": ["bravo"],
        "pack_rotation_mode": "off",
    })
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("SessionStart")
    assert calls[0].endswith("a-start.wav")


def test_empty_rotation_list_keeps_the_configured_pack(paths, monkeypatch):
    _two_session_packs(paths)
    write_config(paths, {
        "pack": "alpha",
        "pack_rotation": [],
        "pack_rotation_mode": "round-robin",
    })
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("SessionStart")
    assert calls[0].endswith("a-start.wav")


def test_play_follows_the_event_map_overlay(paths, monkeypatch):
    make_pack(paths / "packs", "peon", {
        "task.complete": [("done.wav", "done")],
        "task.acknowledge": [("ack.wav", "ack")],
    })
    write_config(paths, {"event_map": {"Stop": "task.acknowledge"}})
    calls = _capture_player(monkeypatch)
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("Stop")
    assert len(calls) == 1
    assert calls[0].endswith("ack.wav")


def test_rapid_prompts_play_the_spam_sound(played, paths, monkeypatch):
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("UserPromptSubmit")
    feed_stdin(monkeypatch, {})
    peon_event.cmd_event("UserPromptSubmit")
    # First prompt has no `task.acknowledge` sound in this pack, the second is spam.
    assert [p for p, _ in played] == [str((paths / "packs" / "peon" / "sounds" / "angry.wav").resolve())]


# --- venv gating ---

def test_player_python_is_the_system_interpreter_until_the_venv_is_built(paths):
    # Windows swaps in pythonw.exe when it sits next to the interpreter.
    assert peon_event.player_python() == peon_event._windowless(sys.executable)


def test_player_python_switches_to_the_venv_when_ready(paths):
    config.VENV_PY.parent.mkdir(parents=True)
    config.VENV_PY.write_text("#!/bin/sh\n", encoding="utf-8")
    config.VENV_STATE_PATH.write_text(
        json.dumps(peon_event.expected_runtime_state()), encoding="utf-8")
    assert peon_event.venv_ready() is True
    assert peon_event.player_python() == peon_event._windowless(str(config.VENV_PY))


def test_a_stale_venv_state_is_not_accepted(paths):
    config.VENV_PY.parent.mkdir(parents=True)
    config.VENV_PY.write_text("#!/bin/sh\n", encoding="utf-8")
    config.VENV_STATE_PATH.write_text(
        json.dumps({"plugin_version": "0.0.1", "dependencies": []}), encoding="utf-8")
    assert peon_event.venv_ready() is False


def test_playing_without_a_venv_triggers_one_background_install(paths, monkeypatch):
    make_pack(paths / "packs", "peon", THREE)
    monkeypatch.setattr(peon_event, "spawn_player", lambda p, v: None)
    spawns = []
    monkeypatch.setattr(peon_event, "spawn_venv_installer", lambda: spawns.append(1))
    peon_event.play_category("session.start")
    assert spawns == [1]


def test_the_install_lock_stops_a_second_worker(paths):
    assert peon_event.venv_install_lock_is_fresh() is False
    assert peon_event.venv_install_lock_is_fresh() is True


# --- doctor reports packs whose audio fails the spec 4.4 check ---

def test_bad_audio_files_lists_only_the_liars(paths):
    d = make_pack(paths / "packs", "peon",
                  {"session.start": [("good.wav", "hi"), ("bad.wav", "nope")]})
    (d / "sounds" / "bad.wav").write_bytes(b"not audio at all")
    assert peon_event.bad_audio_files(d) == ["sounds/bad.wav"]


def test_bad_audio_files_is_empty_for_a_healthy_pack(paths):
    d = make_pack(paths / "packs", "peon", {"session.start": [("good.wav", "hi")]})
    assert peon_event.bad_audio_files(d) == []


def test_doctor_reports_the_audio_check(paths, capsys):
    d = make_pack(paths / "packs", "peon", {"session.start": [("bad.wav", "nope")]})
    (d / "sounds" / "bad.wav").write_bytes(b"not audio at all")
    peon_event.cmd_doctor()
    out = capsys.readouterr().out
    assert "pack audio   : 1 file(s) fail" in out
    assert "sounds/bad.wav" in out
