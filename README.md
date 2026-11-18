# peon-py

<p align="center">
  <img src="assets/logo.svg" alt="peon-py logo" width="120" height="120">
</p>

Another [openpeon](https://openpeon.com/) implementation for Claude Code / Codex plugin.

## Why

The official implementation [peon-ping](https://www.peonping.com/) install in `~/.claude/hooks`. That's crazy! We provides peon in plugin, which is pluggable and manageable.

## Install

### Claude Code

If you didn't add [my plugin marketplace](https://github.com/xchacha20-poly1305/agent-plugins), add it first.


```shell
claude plugin marketplace add https://github.com/xchacha20-poly1305/agent-plugins.git
```

Then you can install it:

```shell
claude plugin install peon-py@anrong-plugins
```

Ask your agents to initialize it:

```
/plugin install peon-py
```

or point `/plugin` at a local clone. Then grab a sound pack:

```
/peon-install peon
/peon-test
```

Update the plugin:

```shell
claude plugin update peon-py@anrong-plugins
```

### Codex

Add my plugin marketplace if not have:

```shell
codex plugin marketplace add https://github.com/xchacha20-poly1305/agent-plugins.git
```

Install the plugin:

```shell
codex plugin add peon-py@anrong-plugins
```

You can also install it from the TUI: launch `codex`, run `/plugins`, pick `peon-py@anrong-plugins`, and confirm.

Enable `plugin_hooks` feature in `~/.codex/config.toml`:

```toml
[features]
plugin_hooks = true
```

Then trust the hooks in TUI: run `/hooks`, enter each hook, then press `t` to trust.

Then ask your agent to initialize it:

```
$peon-py-use Hey codex, can you help me to initialize this plugin? I want to use the sound pack <Describe what you need here>...
```

Update after pulling new changes:

```shell
codex plugin add peon-py@anrong-plugins
```

## Commands

| Command | What it does |
|---|---|
| `/peon-install <pack>` | Download and install a pack from the OpenPeon registry |
| `/peon-list` | Installed packs, with the active one marked |
| `/peon-set <pack>` | Switch the active pack |
| `/peon-volume [0.0-1.0]` | Get or set the master volume |
| `/peon-mute [on\|off]` | Mute or unmute everything |
| `/peon-category [name [on\|off]]` | List or toggle a CESP category |
| `/peon-test [category]` | Play one sound right now |
| `/peon-preview [pack]` | Play the installed pack's manifest preview file |
| `/peon-doctor` | Packs, config, audio runtime status |

Everything is also available directly: `scripts/peon_event.py <subcommand>`, plus
`search [query]` for browsing the registry.

## Event mapping

peon-py is a CESP *player*: agent events map onto the nine standard categories
(spec section 7).

| Hook event | CESP category |
|---|---|
| `SessionStart` | `session.start` |
| `SessionEnd` | `session.end` |
| `UserPromptSubmit` | `task.acknowledge` — or `user.spam` on a second prompt within 4 s |
| `SubagentStart` | `task.acknowledge` |
| `Stop`, `SubagentStop`, `TaskCompleted` | `task.complete` |
| `PostToolUseFailure`, `StopFailure` | `task.error` |
| `Notification`, `PermissionRequest`, `Elicitation` | `input.required` |
| `PreCompact` | `resource.limit` |
| `PreToolUse`, `PostToolUse` | `task.progress` *(disabled by default — they fire on every tool call)* |

Categories the active pack has no sounds for are skipped silently, as the spec requires.
Sounds are picked at random without repeating the previous one, and a 500 ms per-category
debounce keeps bursts of events from turning into a burst of audio.

## Configuration

`~/.config/peon-py/config.json`:

```json
{
  "pack": "peon",
  "volume": 0.5,
  "muted": false,
  "debounce_ms": 500,
  "spam_window_ms": 4000,
  "pack_rotation": ["peon", "glados"],
  "pack_rotation_mode": "off",
  "event_map": {
    "Stop": "task.acknowledge"
  },
  "categories": {
    "session.start": true,
    "task.complete": true,
    "task.progress": false
  }
}
```

Set any category to `false` to mute just that one, or run `/peon-category task.progress on`.

`pack_rotation_mode` is `off` (the default — always use `pack`), `random`, or
`round-robin`. With `random` or `round-robin`, each `SessionStart` picks one
*installed* pack from `pack_rotation` and later events in that session keep it.
Names that are missing or not installed are skipped; an empty list behaves like
`off`.

`event_map` overlays the hook-event → CESP-category table (see above). Values
must be one of the nine spec categories; an unknown name is ignored and that
event keeps its documented default. Rapid `UserPromptSubmit` still becomes
`user.spam`.

## Where things live

| Path | What |
|---|---|
| `~/.openpeon/packs/` | Installed packs (spec section 8.2, same on every OS) |
| `./.openpeon/packs/` | Project-local packs, which shadow the global ones |
| `~/.config/peon-py/config.json` | Your settings |
| `~/.local/state/peon-py/` | `event.log`, `install.log`, `play-state.json` |
| `~/.local/share/peon-py/venv/` | Managed audio runtime |

Linux and macOS follow the XDG base directories above (`XDG_CONFIG_HOME` and friends are
honoured). On Windows the settings live in `%APPDATA%\peon-py\` and the logs, play state
and venv in `%LOCALAPPDATA%\peon-py\`. Any of them can be redirected with
`PEON_PY_CONFIG_DIR`, `PEON_PY_STATE_DIR`, `PEON_PY_DATA_DIR`, `PEON_PY_VENV_DIR` and
`PEON_PY_PACKS_DIR`.

## Platform support

| | Playback | Notes |
|---|---|---|
| Linux | `sounddevice` → `pw-play`, `paplay`, `ffplay`, `mpv`, `play`, `aplay` | |
| macOS | `sounddevice` → `afplay` | |
| Windows | `sounddevice` → `winsound` (WAV) → PowerShell `MediaPlayer` | Needs `python3`/`python` on `PATH`, or the `py` launcher's `.py` association, so the agent's shell can start the hook script. |

Everything platform-specific — where files live, how the player process is detached, which
fallback runs — is decided at runtime, and CI runs the suite on Linux, macOS and Windows.

## Audio runtime

Playback uses `soundfile` + `sounddevice` inside a managed virtualenv, installed in the
background on first use from a hash-pinned lockfile (`requirements-uv.lock.txt`, via `uv`
when available and `pip` otherwise). Nothing is installed into your own interpreter.

Until that venv is ready — and on any host where the wheels do not work — playback falls
back to the first system player it finds: `pw-play`, `paplay`, `ffplay`, `mpv`, `play`,
`afplay`, `aplay`. Windows additionally falls back to stdlib `winsound` for WAV and
PowerShell's `MediaPlayer` for the compressed formats, so it is never silent. WAV, MP3,
OGG and FLAC all work either way; `winsound` is the one backend that ignores the volume
setting.

## Making your own pack

Any directory with an `openpeon.json` manifest under `~/.openpeon/packs/` is picked up —
see the [CESP spec](https://openpeon.com/spec). `category_aliases` is honoured in both
directions: spec section 6's order (a legacy name you ask for resolves to the CESP category
the pack stores), plus the reverse — a pack that kept its sounds under the legacy keys and
declared the aliases still plays, which a strict reading of section 6 would leave silent.

Audio files must match their extension's magic bytes (spec 4.4); one that does not is
skipped rather than played, and `/peon-doctor` lists it.

## Development

```bash
python -m pytest -q          # unit tests, no audio hardware needed
python -m compileall -q scripts
scripts/peon_event.py doctor
```

## License

MIT. Sound packs carry their own licenses — check each pack before redistributing it.
