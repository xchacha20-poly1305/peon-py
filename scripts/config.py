"""Shared constants, paths and event tables for peon-py.

peon-py is a CESP v1.0 player: it maps Claude Code / Codex hook events to the
nine standard Coding Event Sound Pack categories and plays a sound from the
active pack. See https://openpeon.com/spec for the specification.

Every path below can be redirected with a `PEON_PY_*` environment variable,
which keeps the tests (and anyone with an unusual XDG setup) out of the real
home directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PLUGIN_VERSION = "0.2.2"
CESP_VERSION = "1.0"

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# --- CESP categories (spec section 1) ---

CORE_CATEGORIES = (
    "session.start",
    "task.acknowledge",
    "task.complete",
    "task.error",
    "input.required",
    "resource.limit",
)
EXTENDED_CATEGORIES = (
    "user.spam",
    "session.end",
    "task.progress",
)
ALL_CATEGORIES = CORE_CATEGORIES + EXTENDED_CATEGORIES

# --- Hook event -> CESP category ---
#
# Follows the reference mapping in CESP section 7.2, extended with the extra
# hook names Claude Code fires today. Events absent from this table are
# ignored silently: players must never invent category names (spec 1.3).
#
# `UserPromptSubmit` is special-cased in peon_event.py: two prompts inside
# `spam_window_ms` map to `user.spam` instead of `task.acknowledge`.
EVENT_CATEGORIES: dict[str, str] = {
    "SessionStart": "session.start",
    "SessionEnd": "session.end",
    "UserPromptSubmit": "task.acknowledge",
    # A sub-agent picking up work is an acknowledgement of its own; a fan-out of
    # several at once collapses into one sound via the per-category debounce.
    "SubagentStart": "task.acknowledge",
    "Stop": "task.complete",
    "SubagentStop": "task.complete",
    "TaskCompleted": "task.complete",
    "PostToolUseFailure": "task.error",
    "StopFailure": "task.error",
    "Notification": "input.required",
    "PermissionRequest": "input.required",
    "Elicitation": "input.required",
    "PreCompact": "resource.limit",
    "PreToolUse": "task.progress",
    "PostToolUse": "task.progress",
}

# Antigravity exposes a smaller, differently-named hook surface (no
# UserPromptSubmit or PostToolUseFailure). Keep EVENT_CATEGORIES exact for
# Claude/Codex and overlay Antigravity defaults when `--agent antigravity`.
ANTIGRAVITY_EVENT_CATEGORIES: dict[str, str] = {
    "SessionStart": "session.start",
    "PreInvocation": "task.acknowledge",
    "PreToolUse": "task.progress",
    "PostToolUse": "task.progress",
    "Stop": "task.complete",
}

ALL_EVENT_NAMES: frozenset[str] = frozenset(EVENT_CATEGORIES) | frozenset(ANTIGRAVITY_EVENT_CATEGORIES)

SPAM_CATEGORY = "user.spam"
SPAM_SOURCE_EVENT = "UserPromptSubmit"

# Categories enabled out of the box. `task.progress` fires on every single
# tool call, so it stays off until the user asks for it.
DEFAULT_CATEGORY_ENABLED: dict[str, bool] = {
    name: name != "task.progress" for name in ALL_CATEGORIES
}

ROTATION_MODES = ("off", "random", "round-robin")

DEFAULT_CONFIG: dict = {
    "pack": None,          # None = use the only installed pack, if there is one
    "volume": 0.5,         # 0.0 - 1.0
    "muted": False,
    "categories": dict(DEFAULT_CATEGORY_ENABLED),
    "debounce_ms": 500,    # per-category floor between two sounds
    "spam_window_ms": 4000,
    "max_log_size": 5 * 1024 * 1024,
    # Spec 8.2 MAY: pick one installed pack from this list on SessionStart.
    # `off` (or an empty list) keeps today's single-pack selection.
    "pack_rotation": [],
    "pack_rotation_mode": "off",
    # Overlay on EVENT_CATEGORIES. Values must be CESP category names;
    # unknown keys/values are dropped at load (spec 1.3).
    "event_map": {},
}


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def platform_dirs(env: dict | None = None, home: Path | None = None,
                  osname: str = os.name) -> tuple[Path, Path, Path]:
    """(config, state, data) roots for this OS.

    Windows keeps roaming config in `%APPDATA%` and machine-local state next to
    the venv in `%LOCALAPPDATA%`; everywhere else the XDG base directories
    apply, which is also what macOS CLI tooling has settled on.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    def _xdg(var: str, fallback: str) -> Path:
        raw = env.get(var, "").strip()
        return Path(raw).expanduser() if raw else home / fallback

    if osname == "nt":
        roaming = env.get("APPDATA", "").strip()
        local = env.get("LOCALAPPDATA", "").strip()
        roaming_dir = Path(roaming) if roaming else home / "AppData" / "Roaming"
        local_dir = Path(local) if local else home / "AppData" / "Local"
        return (roaming_dir / "peon-py",
                local_dir / "peon-py" / "state",
                local_dir / "peon-py")
    return (_xdg("XDG_CONFIG_HOME", ".config") / "peon-py",
            _xdg("XDG_STATE_HOME", ".local/state") / "peon-py",
            _xdg("XDG_DATA_HOME", ".local/share") / "peon-py")


# --- Directories ---

_CONFIG_ROOT, _STATE_ROOT, _DATA_ROOT = platform_dirs()

CONFIG_DIR = _env_path("PEON_PY_CONFIG_DIR", _CONFIG_ROOT)
STATE_DIR = _env_path("PEON_PY_STATE_DIR", _STATE_ROOT)
DATA_DIR = _env_path("PEON_PY_DATA_DIR", _DATA_ROOT)

CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = STATE_DIR / "event.log"
INSTALL_LOG_PATH = STATE_DIR / "install.log"
PLAY_STATE_PATH = STATE_DIR / "play-state.json"

# --- Managed venv (audio stack lives here, never in the user's interpreter) ---

VENV_DIR = _env_path("PEON_PY_VENV_DIR", DATA_DIR / "venv")
VENV_BIN = VENV_DIR / ("Scripts" if os.name == "nt" else "bin")
VENV_PY = VENV_BIN / ("python.exe" if os.name == "nt" else "python")
# Windows-only console-less interpreter: launching the player with it is what
# keeps a terminal window from blinking on screen for every event.
VENV_PYW = VENV_BIN / "pythonw.exe" if os.name == "nt" else None
VENV_STATE_PATH = VENV_DIR / ".peon-runtime.json"
UV_LOCKFILE_PATH = PLUGIN_ROOT / "requirements-uv.lock.txt"

# Top-level audio dependencies. Exact versions (and their hashes) live in the
# lockfile, which pins a different numpy per interpreter; the venv's identity is
# therefore the lockfile digest, not a version list. See `requirements.in`.
RUNTIME_PACKAGES = ("numpy", "soundfile", "sounddevice")


def lockfile_digest() -> str:
    """SHA-256 of the lockfile, used to detect a stale managed venv."""
    import hashlib

    try:
        return hashlib.sha256(UV_LOCKFILE_PATH.read_bytes()).hexdigest()
    except OSError:
        return ""

# --- Pack storage (spec section 8.2) ---

GLOBAL_PACKS_DIR = _env_path("PEON_PY_PACKS_DIR", Path.home() / ".openpeon" / "packs")
PROJECT_PACKS_DIRNAME = ".openpeon/packs"

REGISTRY_INDEX_URL = "https://peonping.github.io/registry/index.json"


def packs_dirs(cwd: Path | None = None) -> list[Path]:
    """Pack search path, most specific first: project-local, then global."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    return [base / PROJECT_PACKS_DIRNAME, GLOBAL_PACKS_DIR]


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, STATE_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_user_config() -> dict:
    """User config merged over the defaults. Never raises."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        raw = json.loads(CONFIG_PATH.read_text("utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for key, value in raw.items():
        if key == "categories" and isinstance(value, dict):
            cfg["categories"].update(
                {k: bool(v) for k, v in value.items() if k in ALL_CATEGORIES}
            )
        elif key == "pack_rotation":
            if isinstance(value, list):
                seen: list[str] = []
                for name in value:
                    if isinstance(name, str):
                        name = name.strip()
                        if name and name not in seen:
                            seen.append(name)
                cfg["pack_rotation"] = seen
        elif key == "pack_rotation_mode":
            if value in ROTATION_MODES:
                cfg["pack_rotation_mode"] = value
        elif key == "event_map":
            if isinstance(value, dict):
                cfg["event_map"] = {
                    event: category
                    for event, category in value.items()
                    if event in ALL_EVENT_NAMES
                    and isinstance(category, str)
                    and category in ALL_CATEGORIES
                }
        elif key in cfg:
            cfg[key] = value
    return cfg


def save_user_config(cfg: dict) -> None:
    ensure_dirs()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def mapped_category(cfg: dict, event: str, agent: str = "") -> str | None:
    """CESP category for a hook event, honouring a user overlay.

    Invented category names in the overlay are ignored and the documented
    default for that event is kept. Events absent from the agent's event
    table still map to nothing.
    """
    overlay = cfg.get("event_map")
    if isinstance(overlay, dict):
        category = overlay.get(event)
        if isinstance(category, str) and category in ALL_CATEGORIES:
            return category
    table = ANTIGRAVITY_EVENT_CATEGORIES if agent == "antigravity" else EVENT_CATEGORIES
    return table.get(event)


def category_enabled(cfg: dict, category: str) -> bool:
    cats = cfg.get("categories")
    if not isinstance(cats, dict):
        cats = DEFAULT_CATEGORY_ENABLED
    return bool(cats.get(category, DEFAULT_CATEGORY_ENABLED.get(category, False)))


def clamp_volume(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["volume"]
    return min(1.0, max(0.0, v))


def truncate_log_if_needed(path: Path, max_size: int) -> None:
    """Keep the tail half of a log once it outgrows `max_size`."""
    try:
        if max_size <= 0 or not path.exists() or path.stat().st_size <= max_size:
            return
        data = path.read_bytes()[-(max_size // 2):]
        # Drop the leading partial line so the file stays parseable.
        newline = data.find(b"\n")
        if newline != -1:
            data = data[newline + 1:]
        path.write_bytes(data)
    except Exception:
        pass
