#!/usr/bin/env python3
"""
Hook entry for peon-py — CESP v1.0 sound feedback for Claude Code and Codex.

Usage:
    peon_event.py <EventName>       # called by the agent's hooks (stdin: hook JSON)
    peon_event.py list              # installed packs
    peon_event.py search [query]    # registry packs
    peon_event.py install <pack>    # install a pack from the registry
    peon_event.py set <pack>        # choose the active pack
    peon_event.py volume [0.0-1.0]  # get/set master volume
    peon_event.py mute [on|off]     # get/toggle global mute
    peon_event.py test [category]   # play one sound now
    peon_event.py preview [pack]    # play the pack's manifest preview file
    peon_event.py category [name [on|off]]  # list or toggle a CESP category
    peon_event.py doctor            # config, packs and runtime status
    peon_event.py venv-install      # internal: build the managed venv

Selection is pure stdlib so the hook returns in milliseconds; the chosen file
is handed to a detached `peon_play.py`. Every error is swallowed and logged to
`event.log` — a hook must never block or fail the coding agent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import pack as pack_mod  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PLAY_SCRIPT = SCRIPT_DIR / "peon_play.py"


def _log(line: str, path: Path | None = None) -> None:
    try:
        target = path or config.LOG_PATH
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
        config.truncate_log_if_needed(
            target, config.load_user_config().get("max_log_size", 5 * 1024 * 1024)
        )
    except Exception:
        pass


def _log_install(line: str) -> None:
    _log(line, config.INSTALL_LOG_PATH)


# ------------------------------- play state -------------------------------
#
# One small JSON file replaces the daemon pet4agents needs: it only has to
# remember the last sound per category (no-repeat), the last play time
# (debounce), and the pack picked for this session when rotation is on.
# Writes are last-one-wins, which is fine for hints like these.


def load_play_state() -> dict:
    try:
        data = json.loads(config.PLAY_STATE_PATH.read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_file", {})
            data.setdefault("last_played_ms", {})
            return data
    except Exception:
        pass
    return {"last_file": {}, "last_played_ms": {}}


def save_play_state(state: dict) -> None:
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.PLAY_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(config.PLAY_STATE_PATH)
    except Exception as e:
        _log(f"failed to write play state: {e}")


def now_ms() -> int:
    return int(time.time() * 1000)


def tick_rotation(cfg: dict, state: dict, rng=None) -> str | None:
    """Pick the pack for a new session and persist it on `state`.

    Called on SessionStart. Missing or uninstalled names are skipped by
    `pack.next_rotated_pack`; an empty/off rotation clears any leftover
    session pick so later plays fall back to the configured pack.
    """
    packs = pack_mod.discover_packs()
    last = state.get("rotation_last")
    last = last if isinstance(last, str) else None
    chosen = pack_mod.next_rotated_pack(cfg, packs, last, rng)
    if chosen is None:
        state.pop("session_pack", None)
        return None
    state["session_pack"] = chosen
    state["rotation_last"] = chosen
    return chosen


def session_preferred(cfg: dict, state: dict | None = None) -> str | None:
    """Session pack to prefer while rotation is on, else None."""
    if pack_mod.rotation_mode(cfg) == "off":
        return None
    if state is None:
        state = load_play_state()
    name = state.get("session_pack")
    return name if isinstance(name, str) and name else None


# ------------------------------ managed venv ------------------------------


def expected_runtime_state() -> dict:
    return {
        "plugin_version": config.PLUGIN_VERSION,
        "lock_sha256": config.lockfile_digest(),
    }


def venv_ready() -> bool:
    """True when the managed venv exists and matches the pinned runtime."""
    try:
        if not config.VENV_PY.exists():
            return False
        state = json.loads(config.VENV_STATE_PATH.read_text("utf-8"))
        return state == expected_runtime_state()
    except Exception:
        return False


def _windowless(exe: str) -> str:
    """`pythonw.exe` beside `exe` on Windows, so no console can appear."""
    if os.name != "nt":
        return exe
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def player_python() -> str:
    """Interpreter for peon_play.py — the venv when it's ready, else ours.

    Falling back to the ambient interpreter keeps sound working while the venv
    builds: peon_play.py degrades to a system audio player when the Python
    audio stack is not importable.
    """
    return _windowless(str(config.VENV_PY) if venv_ready() else sys.executable)


def detached_kwargs() -> dict:
    """Popen kwargs that cut a child loose from the agent's console.

    POSIX gets its own session; Windows has no `setsid`, so it takes the
    equivalent creation flags. `DETACHED_PROCESS` also means the child inherits
    no console, which is what stops a window from flashing on every event.
    """
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _run(cmd: list[str], timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0, r.stderr.decode("utf-8", "replace")
    except Exception as e:  # timeout, missing binary, ...
        return False, str(e)


def install_venv() -> bool:
    """Create the venv and install the pinned, hash-locked audio stack.

    Prefers `uv` (much faster) with an independent fallback to stdlib venv/pip
    for each step, mirroring how pet4agents bootstraps PySide6.
    """
    config.ensure_dirs()
    uv = shutil.which("uv")

    if not config.VENV_PY.exists():
        created = False
        if uv:
            _log_install(f"creating venv at {config.VENV_DIR} (uv)")
            created, err = _run([uv, "venv", "--seed", str(config.VENV_DIR)], 120)
            if not created:
                _log_install(f"uv venv failed: {err}")
        if not created:
            _log_install(f"creating venv at {config.VENV_DIR} (stdlib venv)")
            created, err = _run([sys.executable, "-m", "venv", str(config.VENV_DIR)], 300)
            if not created:
                _log_install(f"venv creation failed: {err}")
                return False

    lock = config.UV_LOCKFILE_PATH
    if not lock.exists():
        _log_install(f"lockfile missing: {lock}")
        return False

    ok = False
    if uv:
        _log_install("installing pinned deps (uv, --require-hashes)")
        ok, err = _run(
            [uv, "pip", "install", "--python", str(config.VENV_PY), "--quiet",
             "--require-hashes", "-r", str(lock)], 900)
        if not ok:
            _log_install(f"uv pip install failed: {err}")
    if not ok:
        _log_install("installing pinned deps (pip, --require-hashes)")
        ok, err = _run(
            [str(config.VENV_PY), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "--require-hashes", "-r", str(lock)], 900)
        if not ok:
            _log_install(f"pip install failed: {err}")
            return False

    try:
        config.VENV_STATE_PATH.write_text(
            json.dumps(expected_runtime_state(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    except Exception as e:
        _log_install(f"failed to write runtime state: {e}")
        return False
    _log_install("managed venv ready")
    return True


def venv_install_lock_is_fresh(max_age_s: int = 1800) -> bool:
    """True if another install worker is (probably) already running."""
    marker = config.STATE_DIR / "venv-install.lock"
    try:
        if marker.exists() and (time.time() - marker.stat().st_mtime) < max_age_s:
            return True
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    return False


def spawn_venv_installer() -> None:
    """Detached worker that builds the venv; the hook never waits for it."""
    if venv_install_lock_is_fresh():
        return
    try:
        config.ensure_dirs()
        log_f = config.INSTALL_LOG_PATH.open("a", encoding="utf-8")
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "venv-install"],
            stdin=subprocess.DEVNULL, stdout=log_f, stderr=log_f,
            **detached_kwargs())
        _log_install("spawned venv installer")
    except Exception as e:
        _log_install(f"failed to spawn venv installer: {e}")


# -------------------------------- playback --------------------------------


def spawn_player(path: Path, volume: float) -> None:
    """Fire-and-forget the detached player process."""
    try:
        subprocess.Popen(
            [player_python(), str(PLAY_SCRIPT), str(path), f"{volume:.4f}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached_kwargs())
    except Exception as e:
        _log(f"failed to spawn player for {path}: {e}")


def play_category(category: str, cfg: dict | None = None, *,
                  respect_debounce: bool = True) -> str | None:
    """Select and play one sound for `category`. Returns the label, if any."""
    cfg = cfg if cfg is not None else config.load_user_config()
    if cfg.get("muted"):
        return None
    if not config.category_enabled(cfg, category):
        return None

    state = load_play_state()
    now = now_ms()
    if respect_debounce:
        # Spec 7.3: debounce rapid events instead of firing a burst of sounds.
        last = state["last_played_ms"].get(category, 0)
        if isinstance(last, (int, float)) and now - last < int(cfg.get("debounce_ms", 500)):
            return None

    selected = pack_mod.active_pack(cfg, preferred=session_preferred(cfg, state))
    if selected is None:
        _log(f"no pack installed; skipping {category}")
        return None
    pack_name, pack_dir = selected
    try:
        manifest = pack_mod.load_manifest(pack_dir)
    except pack_mod.PackError as e:
        _log(f"bad pack {pack_name}: {e}")
        return None

    choice = pack_mod.select_sound(
        manifest, pack_dir, category, state["last_file"].get(category))
    if choice is None:
        return None  # pack has nothing for this category — silent skip (spec 7.1)
    path, entry = choice

    state["last_file"][category] = str(path)
    state["last_played_ms"][category] = now
    save_play_state(state)

    if not venv_ready():
        spawn_venv_installer()
    spawn_player(path, config.clamp_volume(cfg.get("volume")))
    return entry.get("label") or path.name


# --------------------------------- events ---------------------------------


def read_hook_payload() -> dict:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def category_for_event(event: str, cfg: dict, state: dict, now: int) -> str | None:
    """Map a hook event to a CESP category, handling the spam special case."""
    category = config.mapped_category(cfg, event)
    if category is None:
        return None
    if event == config.SPAM_SOURCE_EVENT:
        last = state["last_played_ms"].get(config.SPAM_SOURCE_EVENT, 0)
        window = int(cfg.get("spam_window_ms", 4000))
        state["last_played_ms"][config.SPAM_SOURCE_EVENT] = now
        if isinstance(last, (int, float)) and last and now - last < window:
            return config.SPAM_CATEGORY
    return category


def cmd_event(event: str) -> None:
    read_hook_payload()  # drained so the agent's pipe never blocks
    cfg = config.load_user_config()

    state = load_play_state()
    now = now_ms()
    if event == "SessionStart":
        tick_rotation(cfg, state)
    category = category_for_event(event, cfg, state, now)
    # `category_for_event` records the prompt timestamp used for spam detection.
    save_play_state(state)
    if category is None:
        return
    play_category(category, cfg)


# ------------------------------- subcommands ------------------------------


def cmd_list() -> int:
    cfg = config.load_user_config()
    packs = pack_mod.discover_packs()
    if not packs:
        print("No sound packs installed.")
        print(f"Install one with: peon_event.py install peon  (-> {config.GLOBAL_PACKS_DIR})")
        return 0
    selected = pack_mod.active_pack(cfg, preferred=session_preferred(cfg))
    active_name = selected[0] if selected else None
    for name in sorted(packs):
        marker = "*" if name == active_name else " "
        try:
            manifest = pack_mod.load_manifest(packs[name])
            cats = ", ".join(sorted(manifest.get("categories", {})))
            print(f"{marker} {name}  ({manifest.get('display_name', name)})")
            print(f"    {packs[name]}")
            print(f"    categories: {cats or 'none'}")
        except pack_mod.PackError as e:
            print(f"{marker} {name}  [invalid: {e}]")
    print("\n* = active pack")
    return 0


def cmd_search(query: str = "") -> int:
    import registry
    try:
        entries = registry.fetch_index()
    except registry.RegistryError as e:
        print(f"error: {e}")
        return 1
    q = query.lower().strip()
    if q:
        entries = [
            e for e in entries
            if q in e.get("name", "").lower()
            or q in e.get("display_name", "").lower()
            or q in " ".join(e.get("tags", [])).lower()
        ]
    for entry in entries[:40]:
        print(f"{entry['name']:<24} {entry.get('display_name', '')} "
              f"[{entry.get('sound_count', '?')} sounds, {entry.get('trust_tier', '?')}]")
    print(f"\n{len(entries)} pack(s) matched. Install with: peon_event.py install <name>")
    return 0


def cmd_install(name: str) -> int:
    import registry
    try:
        dest = registry.install_pack(name)
        sounds, verified = registry.verify_pack(dest)
    except (registry.RegistryError, pack_mod.PackError) as e:
        print(f"error: {e}")
        return 1
    print(f"installed {name} -> {dest} ({sounds} sounds, {verified} checksums verified)")

    cfg = config.load_user_config()
    if not cfg.get("pack"):
        cfg["pack"] = name
        config.save_user_config(cfg)
        print(f"active pack set to {name}")
    if not venv_ready():
        spawn_venv_installer()
        print("building the audio runtime in the background "
              f"(progress: {config.INSTALL_LOG_PATH})")
    return 0


def cmd_set(name: str) -> int:
    packs = pack_mod.discover_packs()
    if name not in packs:
        print(f"pack {name!r} is not installed. Installed: {', '.join(sorted(packs)) or 'none'}")
        return 1
    cfg = config.load_user_config()
    cfg["pack"] = name
    config.save_user_config(cfg)
    print(f"active pack set to {name} ({packs[name]})")
    return 0


def cmd_volume(value: str = "") -> int:
    cfg = config.load_user_config()
    if not value:
        print(f"volume: {cfg['volume']}")
        return 0
    try:
        float(value)
    except ValueError:
        print(f"invalid volume {value!r}; expected a number between 0.0 and 1.0")
        return 1
    cfg["volume"] = config.clamp_volume(value)
    config.save_user_config(cfg)
    print(f"volume set to {cfg['volume']}")
    return 0


def cmd_mute(value: str = "") -> int:
    cfg = config.load_user_config()
    word = value.strip().lower()
    if not word:
        cfg["muted"] = not cfg.get("muted")
    elif word in ("on", "true", "yes", "1"):
        cfg["muted"] = True
    elif word in ("off", "false", "no", "0"):
        cfg["muted"] = False
    else:
        print(f"usage: peon_event.py mute [on|off] (got {value!r})")
        return 1
    config.save_user_config(cfg)
    print("sounds muted" if cfg["muted"] else "sounds unmuted")
    return 0


def cmd_test(category: str = "") -> int:
    category = category.strip() or "session.start"
    if category not in config.ALL_CATEGORIES:
        print(f"unknown category {category!r}; valid: {', '.join(config.ALL_CATEGORIES)}")
        return 1
    cfg = config.load_user_config()
    if cfg.get("muted"):
        print("sounds are muted; run `peon_event.py mute off` first")
        return 1
    label = play_category(category, cfg, respect_debounce=False)
    if label is None:
        print(f"nothing to play for {category} "
              "(category disabled, or the active pack has no sounds for it)")
        return 1
    print(f"playing {category}: {label}")
    if not venv_ready():
        print("audio runtime still building; using a system player for now")
    return 0


def cmd_preview(name: str = "") -> int:
    """Play the installed pack's manifest `preview` file. No category fallback."""
    cfg = config.load_user_config()
    if cfg.get("muted"):
        print("sounds are muted; run `peon_event.py mute off` first")
        return 1
    packs = pack_mod.discover_packs()
    name = name.strip()
    if name:
        if name not in packs:
            print(f"pack {name!r} is not installed. Installed: {', '.join(sorted(packs)) or 'none'}")
            return 1
        pack_name, pack_dir = name, packs[name]
    else:
        selected = pack_mod.active_pack(cfg, preferred=session_preferred(cfg))
        if selected is None:
            print("No sound packs installed.")
            return 1
        pack_name, pack_dir = selected
    try:
        manifest = pack_mod.load_manifest(pack_dir)
    except pack_mod.PackError as e:
        print(f"error: {e}")
        return 1
    preview = manifest.get("preview")
    if not isinstance(preview, str) or not preview.strip():
        print(f"{pack_name} has no preview")
        return 1
    try:
        path = pack_mod.resolve_sound_path(pack_dir, preview, prefix_sounds=False)
    except pack_mod.PackError:
        print(f"{pack_name}: preview path is not usable")
        return 1
    if not path.is_file():
        print(f"{pack_name}: preview file is missing ({preview})")
        return 1
    if not venv_ready():
        spawn_venv_installer()
    spawn_player(path, config.clamp_volume(cfg.get("volume")))
    print(f"playing preview: {pack_name} ({path.name})")
    return 0


def cmd_category(name: str = "", value: str = "") -> int:
    """List CESP categories, or enable/disable one by name."""
    cfg = config.load_user_config()
    name = name.strip()
    if not name:
        width = max(len(c) for c in config.ALL_CATEGORIES)
        for c in config.ALL_CATEGORIES:
            flag = "on" if config.category_enabled(cfg, c) else "off"
            print(f"{c:<{width}}  {flag}")
        return 0
    if name not in config.ALL_CATEGORIES:
        print(f"unknown category {name!r}; valid: {', '.join(config.ALL_CATEGORIES)}")
        return 1
    word = value.strip().lower()
    if not word:
        flag = "on" if config.category_enabled(cfg, name) else "off"
        print(f"{name}: {flag}")
        return 0
    if word in ("on", "true", "yes", "1"):
        enabled = True
    elif word in ("off", "false", "no", "0"):
        enabled = False
    else:
        print(f"usage: peon_event.py category [<name> [on|off]] (got {value!r})")
        return 1
    cfg.setdefault("categories", dict(config.DEFAULT_CATEGORY_ENABLED))
    cfg["categories"][name] = enabled
    config.save_user_config(cfg)
    print(f"{name}: {'on' if enabled else 'off'}")
    return 0


def bad_audio_files(pack_dir: Path) -> list[str]:
    """Sounds in `pack_dir` whose bytes do not match their extension.

    Only `doctor` walks a whole pack like this; the playback path checks the
    one file it is about to play.
    """
    bad: list[str] = []
    try:
        manifest = pack_mod.load_manifest(pack_dir)
    except pack_mod.PackError:
        return bad
    for category in manifest.get("categories", {}):
        for entry in pack_mod.resolve_category(manifest, category):
            try:
                path = pack_mod.resolve_sound_path(pack_dir, entry["file"])
            except pack_mod.PackError:
                bad.append(entry["file"])
                continue
            if path.is_file() and not pack_mod.looks_like_audio(path):
                bad.append(entry["file"])
    return sorted(set(bad))


def cmd_doctor() -> int:
    cfg = config.load_user_config()
    packs = pack_mod.discover_packs()
    selected = pack_mod.active_pack(cfg, preferred=session_preferred(cfg))
    print(f"peon-py {config.PLUGIN_VERSION} (CESP {config.CESP_VERSION})")
    print(f"platform     : {sys.platform} (os.name={os.name}), python {sys.version.split()[0]}")
    print(f"config       : {config.CONFIG_PATH}")
    print(f"logs         : {config.LOG_PATH}")
    print(f"pack search  : {', '.join(str(p) for p in config.packs_dirs())}")
    print(f"installed    : {', '.join(sorted(packs)) or 'none'}")
    print(f"active pack  : {selected[0] if selected else 'none'}")
    mode = pack_mod.rotation_mode(cfg)
    if mode == "off":
        print("rotation     : off")
    else:
        listed = ", ".join(
            n for n in (cfg.get("pack_rotation") or []) if isinstance(n, str)
        ) or "(empty)"
        print(f"rotation     : {mode} [{listed}]  "
              f"session: {session_preferred(cfg) or 'none yet'}")
    if selected:
        bad = bad_audio_files(selected[1])
        status = "ok" if not bad else f"{len(bad)} file(s) fail the spec 4.4 magic-byte check"
        print(f"pack audio   : {status}")
        for rel in bad[:5]:
            print(f"  rejected   : {rel}")
    print(f"volume       : {cfg['volume']}    muted: {bool(cfg['muted'])}")
    print(f"debounce     : {cfg['debounce_ms']} ms   spam window: {cfg['spam_window_ms']} ms")
    disabled = [c for c in config.ALL_CATEGORIES if not config.category_enabled(cfg, c)]
    print(f"disabled cats: {', '.join(disabled) or 'none'}")
    print(f"venv         : {config.VENV_DIR} "
          f"[{'ready' if venv_ready() else 'not built'}]")
    print(f"player python: {player_python()}")
    sys.path.insert(0, str(SCRIPT_DIR))
    import peon_play
    print(f"fallback play: {peon_play.system_player_name() or 'none found'}")
    return 0


def split_agent(args: list[str]) -> tuple[list[str], str]:
    """Pull `--agent <name>` out of argv, defaulting to `$PEON_AGENT`.

    The hooks pass the host agent as a flag rather than an environment prefix:
    `VAR=value cmd` is POSIX shell syntax, and Claude Code runs hook commands
    through PowerShell on Windows hosts without Git Bash.
    """
    rest: list[str] = []
    agent = os.environ.get("PEON_AGENT", "").strip()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--agent" and i + 1 < len(args):
            agent, i = args[i + 1], i + 2
            continue
        if arg.startswith("--agent="):
            agent, i = arg.split("=", 1)[1], i + 1
            continue
        rest.append(arg)
        i += 1
    return rest, agent


def main() -> int:
    args, agent = split_agent(sys.argv[1:])
    if agent:
        os.environ["PEON_AGENT"] = agent
    if not args:
        print(__doc__)
        return 0
    sub, rest = args[0], args[1:]

    handlers = {
        "list": lambda: cmd_list(),
        "search": lambda: cmd_search(rest[0] if rest else ""),
        "install": lambda: cmd_install(rest[0]) if rest else _usage("install <pack>"),
        "set": lambda: cmd_set(rest[0]) if rest else _usage("set <pack>"),
        "volume": lambda: cmd_volume(rest[0] if rest else ""),
        "mute": lambda: cmd_mute(rest[0] if rest else ""),
        "test": lambda: cmd_test(rest[0] if rest else ""),
        "preview": lambda: cmd_preview(rest[0] if rest else ""),
        "category": lambda: cmd_category(
            rest[0] if rest else "", rest[1] if len(rest) > 1 else ""),
        "doctor": lambda: cmd_doctor(),
        "venv-install": lambda: 0 if install_venv() else 1,
    }
    handler = handlers.get(sub)
    if handler is not None:
        return handler()

    cmd_event(sub)
    return 0


def _usage(form: str) -> int:
    print(f"usage: peon_event.py {form}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        _log(f"top-level exception: {e}\n{traceback.format_exc()}")
        # Hooks must never fail the agent, so exit 0 even on a crash.
        sys.exit(0)
