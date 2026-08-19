"""The shipped plugin/hook manifests must stay in sync with the code."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import config
import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text("utf-8"))


@pytest.mark.parametrize("path", [
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "hooks/hooks.json",
    "hooks/codex-hooks.json",
])
def test_manifest_is_valid_json(path):
    assert isinstance(load(path), dict)


@pytest.mark.parametrize("path", [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"])
def test_plugin_version_matches_the_code(path):
    plugin = load(path)
    assert plugin["name"] == "peon-py"
    assert plugin["version"] == config.PLUGIN_VERSION


@pytest.mark.parametrize("path,root_var,agent", [
    ("hooks/hooks.json", "CLAUDE_PLUGIN_ROOT", "claude"),
    ("hooks/codex-hooks.json", "PLUGIN_ROOT", "codex"),
])
def test_every_hook_event_is_one_we_map(path, root_var, agent):
    hooks = load(path)["hooks"]
    assert set(hooks) == set(config.EVENT_CATEGORIES)
    for event, matchers in hooks.items():
        for entry in matchers:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                assert f"${{{root_var}}}/scripts/peon_event.py" in hook["command"]
                # No `VAR=value` prefix: the command has to parse under sh,
                # Git Bash and PowerShell alike (see split_agent).
                assert hook["command"].endswith(f" {event} --agent {agent}")
                assert "=" not in hook["command"].split(" ")[0]
                assert hook["timeout"] >= 5


def test_claude_hooks_are_async():
    # Claude Code runs these out of band; Codex ignores the flag and runs them
    # synchronously, which is why the Codex timeouts stay short.
    for matchers in load("hooks/hooks.json")["hooks"].values():
        assert all(h["async"] is True for e in matchers for h in e["hooks"])


def test_session_start_may_take_long_enough_to_start_the_runtime_build():
    hook = load("hooks/hooks.json")["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["timeout"] >= 60


def test_codex_plugin_points_at_its_hook_file():
    assert load(".codex-plugin/plugin.json")["hooks"] == "./hooks/codex-hooks.json"


@pytest.mark.parametrize("script", ["peon_event.py", "peon_play.py"])
def test_hook_scripts_are_executable_with_a_shebang(script):
    path = ROOT / "scripts" / script
    assert path.read_text("utf-8").startswith("#!/usr/bin/env python3")
    if os.name != "nt":  # Windows has no execute bit; the shebang is enough
        assert os.stat(path).st_mode & stat.S_IXUSR


def test_the_lockfile_pins_every_runtime_package():
    locked = {
        line.split("==")[0].strip()
        for line in config.UV_LOCKFILE_PATH.read_text("utf-8").splitlines()
        if "==" in line and not line.startswith(("#", " "))
    }
    assert set(config.RUNTIME_PACKAGES) <= locked


def test_the_lockfile_carries_hashes():
    assert "--hash=sha256:" in config.UV_LOCKFILE_PATH.read_text("utf-8")


def test_the_lockfile_covers_every_supported_interpreter():
    # numpy has no single release with wheels for both 3.10 and 3.14, so the
    # lock must carry a marker-split pin rather than one version.
    lines = [l for l in config.UV_LOCKFILE_PATH.read_text("utf-8").splitlines()
             if l.startswith("numpy==")]
    assert len(lines) > 1
    assert any("python_full_version < '3.11'" in l for l in lines)


def test_every_command_declares_its_script():
    for md in sorted((ROOT / "commands").glob("*.md")):
        text = md.read_text("utf-8")
        assert text.startswith("---")
        assert "description:" in text
        assert "scripts/peon_event.py" in text
