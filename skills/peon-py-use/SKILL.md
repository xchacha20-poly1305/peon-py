---
name: peon-py-use
description: Control peon-py sound packs. Use when the user asks to install, list or switch OpenPeon/CESP sound packs, change the volume, mute the sounds, or debug why no audio plays during a coding session.
---

# peon-py

peon-py plays OpenPeon (CESP v1.0) sound packs on agent lifecycle events. Its hooks run
`scripts/peon_event.py`, which maps each event to a CESP category and plays a sound from
the active pack.

Every operation goes through that one script. Run it from the plugin root — in Claude Code
that is `${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py`, in Codex `${PLUGIN_ROOT}/scripts/peon_event.py`,
in Antigravity `./scripts/peon_event.py` inside the installed plugin directory.
The examples below use `<plugin>/scripts/peon_event.py` for whichever applies.

In Claude Code the same operations are also available as `/peon-install`, `/peon-list`,
`/peon-set`, `/peon-volume`, `/peon-mute`, `/peon-category`, `/peon-test`, `/peon-preview`
and `/peon-doctor` — prefer those when the user typed one of them.

## Install a pack

```bash
<plugin>/scripts/peon_event.py search <query>     # browse the registry
<plugin>/scripts/peon_event.py install <pack>     # install into ~/.openpeon/packs/
```

`peon` (Warcraft III orc peon) is the classic starting pack.

## Inspect and switch packs

```bash
<plugin>/scripts/peon_event.py list
<plugin>/scripts/peon_event.py set <pack>
```

To rotate between several installed packs, edit `pack_rotation` (an array of pack names)
and `pack_rotation_mode` (`off`, `random`, or `round-robin`) in the config file. Each
session start picks one *installed* name from the list; missing names are skipped. An
empty list or `off` keeps using `pack`.

## Volume and mute

```bash
<plugin>/scripts/peon_event.py volume        # print current volume
<plugin>/scripts/peon_event.py volume 0.3    # set it
<plugin>/scripts/peon_event.py mute          # toggle
<plugin>/scripts/peon_event.py mute off
<plugin>/scripts/peon_event.py category                 # list the nine CESP categories
<plugin>/scripts/peon_event.py category task.progress on
```

## Debug

```bash
<plugin>/scripts/peon_event.py doctor        # packs, active pack, venv, fallback player
<plugin>/scripts/peon_event.py test task.complete
<plugin>/scripts/peon_event.py preview       # play the active pack's preview file, if any
<plugin>/scripts/peon_event.py preview glados
```

Config lives at `~/.config/peon-py/config.json`; logs at
`~/.local/state/peon-py/event.log` and `install.log`. The managed audio venv is
`~/.local/share/peon-py/venv/` — while it is still building, playback falls back to a
system player (`pw-play`, `paplay`, `ffplay`, `mpv`, `play`, `aplay`).

On Windows those paths move to `%APPDATA%\peon-py\` (config) and `%LOCALAPPDATA%\peon-py\`
(logs, play state, venv). `doctor` prints the ones actually in effect, so read them from its
output rather than guessing.

Per-category muting is `category <name> on|off`; the nine CESP categories are
`session.start`, `session.end`, `task.acknowledge`, `task.complete`, `task.error`,
`task.progress`, `input.required`, `resource.limit`, `user.spam`.

To remap a hook event onto a different CESP category, set `event_map` in the config file
(e.g. `"Stop": "task.acknowledge"`). Only the nine spec category names are accepted;
anything else is dropped and that event keeps its documented default. Two
`UserPromptSubmit` events inside the spam window still play `user.spam`.
