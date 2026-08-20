"""Config defaults, merging and the CESP category tables."""
from __future__ import annotations

import json

import config
import pytest

from .conftest import write_config


def test_event_table_only_uses_spec_categories():
    # Spec 1.3: players must not invent category names.
    assert set(config.EVENT_CATEGORIES.values()) <= set(config.ALL_CATEGORIES)
    assert config.SPAM_CATEGORY in config.ALL_CATEGORIES


def test_we_cover_every_event_the_reference_player_hooks():
    # peon-ping is the reference CESP player; its Claude Code hook set is the
    # floor for ours. Anything it registers, we register too.
    peon_ping_events = {
        "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop",
        "UserPromptSubmit", "Stop", "Notification", "PermissionRequest",
        "PreToolUse", "PostToolUseFailure", "PreCompact",
    }
    assert peon_ping_events <= set(config.EVENT_CATEGORIES)


def test_per_tool_events_stay_off_by_default():
    # PreToolUse/PostToolUse fire on every single tool call; peon-ping keeps
    # them silent and we get the same result by parking them in task.progress.
    for event in ("PreToolUse", "PostToolUse"):
        category = config.EVENT_CATEGORIES[event]
        assert config.DEFAULT_CATEGORY_ENABLED[category] is False


def test_every_core_category_is_reachable_from_some_event():
    mapped = set(config.EVENT_CATEGORIES.values()) | {config.SPAM_CATEGORY}
    assert set(config.CORE_CATEGORIES) <= mapped


def test_defaults_when_no_config_file(paths):
    cfg = config.load_user_config()
    assert cfg["volume"] == 0.5
    assert cfg["muted"] is False
    assert cfg["pack"] is None
    assert cfg["categories"]["task.complete"] is True
    # task.progress fires on every tool call, so it stays off by default.
    assert cfg["categories"]["task.progress"] is False
    assert cfg["pack_rotation"] == []
    assert cfg["pack_rotation_mode"] == "off"
    assert cfg["event_map"] == {}


def test_user_values_merge_over_defaults(paths):
    write_config(paths, {"volume": 0.2, "categories": {"task.progress": True}})
    cfg = config.load_user_config()
    assert cfg["volume"] == 0.2
    assert cfg["categories"]["task.progress"] is True
    assert cfg["categories"]["task.complete"] is True  # default preserved
    assert cfg["debounce_ms"] == 500


def test_unknown_keys_and_categories_are_ignored(paths):
    write_config(paths, {"nonsense": 1, "categories": {"task.explode": True}})
    cfg = config.load_user_config()
    assert "nonsense" not in cfg
    assert "task.explode" not in cfg["categories"]


def test_rotation_list_is_sanitised_and_mode_is_checked(paths):
    write_config(paths, {
        "pack_rotation": ["peon", "peon", " glados ", 3, ""],
        "pack_rotation_mode": "round-robin",
    })
    cfg = config.load_user_config()
    assert cfg["pack_rotation"] == ["peon", "glados"]
    assert cfg["pack_rotation_mode"] == "round-robin"


def test_an_unknown_rotation_mode_keeps_the_default(paths):
    write_config(paths, {"pack_rotation_mode": "session_override"})
    assert config.load_user_config()["pack_rotation_mode"] == "off"


def test_a_non_list_rotation_is_ignored(paths):
    write_config(paths, {"pack_rotation": "peon"})
    assert config.load_user_config()["pack_rotation"] == []


def test_event_map_overlay_keeps_only_spec_categories(paths):
    write_config(paths, {
        "event_map": {
            "Stop": "task.acknowledge",
            "SessionStart": "task.explode",
            "NotAHook": "task.complete",
            "PreCompact": 1,
        },
    })
    cfg = config.load_user_config()
    assert cfg["event_map"] == {"Stop": "task.acknowledge"}


def test_mapped_category_uses_the_overlay_then_the_default(paths):
    cfg = {
        "event_map": {"Stop": "task.acknowledge", "SessionStart": "task.explode"},
    }
    assert config.mapped_category(cfg, "Stop") == "task.acknowledge"
    # Invented overlay value: keep the documented default.
    assert config.mapped_category(cfg, "SessionStart") == "session.start"
    assert config.mapped_category(cfg, "PreCompact") == "resource.limit"
    assert config.mapped_category(cfg, "SomeFutureHook") is None


def test_corrupt_config_falls_back_to_defaults(paths):
    (paths / "config" / "config.json").write_text("{not json", encoding="utf-8")
    assert config.load_user_config()["volume"] == 0.5


def test_save_round_trip(paths):
    cfg = config.load_user_config()
    cfg["pack"] = "peon"
    config.save_user_config(cfg)
    assert json.loads(config.CONFIG_PATH.read_text())["pack"] == "peon"
    assert config.load_user_config()["pack"] == "peon"


@pytest.mark.parametrize("raw,expected", [
    (0.5, 0.5), ("0.25", 0.25), (-1, 0.0), (7, 1.0), ("loud", 0.5), (None, 0.5),
])
def test_clamp_volume(raw, expected):
    assert config.clamp_volume(raw) == expected


def test_category_enabled_handles_a_broken_categories_block(paths):
    assert config.category_enabled({"categories": "nope"}, "task.complete") is True
    assert config.category_enabled({}, "task.progress") is False


def test_packs_dirs_prefers_the_project_directory(paths):
    dirs = config.packs_dirs(paths / "project")
    assert dirs[0] == paths / "project" / ".openpeon" / "packs"
    assert dirs[1] == config.GLOBAL_PACKS_DIR


def test_log_truncation_keeps_the_tail(paths):
    log = paths / "state" / "big.log"
    log.write_text("".join(f"line {i}\n" for i in range(5000)), encoding="utf-8")
    config.truncate_log_if_needed(log, 1000)
    text = log.read_text()
    assert len(text) <= 1000
    assert text.startswith("line ")     # no partial leading line
    assert text.endswith("line 4999\n")


def test_log_truncation_leaves_small_files_alone(paths):
    log = paths / "state" / "small.log"
    log.write_text("hello\n", encoding="utf-8")
    config.truncate_log_if_needed(log, 1000)
    assert log.read_text() == "hello\n"
