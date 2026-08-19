# peon-py

A Claude Code / Codex plugin that plays [OpenPeon](https://openpeon.com) **CESP v1.0**
sound packs on agent lifecycle events. We are a *player*, not a pack author: no audio
lives in this repo.

## Layout

```
.claude-plugin/plugin.json   Claude Code manifest
.codex-plugin/plugin.json    Codex manifest (points at hooks/codex-hooks.json)
hooks/hooks.json             Claude Code hooks   -> ${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py
hooks/codex-hooks.json       Codex hooks         -> ${PLUGIN_ROOT}/scripts/peon_event.py
commands/                    /peon-* slash commands
skills/peon-py-use/          Agent-neutral skill wrapping the same subcommands
scripts/config.py            Paths, event->category table, pinned deps
scripts/pack.py              Manifest loading, path safety, sound selection
scripts/registry.py          Registry index, download, extraction, validation
scripts/peon_event.py        Hook entry point + every subcommand
scripts/peon_play.py         Detached player (venv audio stack, system fallback)
```

## Design rules

- **The hook process is stdlib-only and fast.** `peon_event.py` and everything it imports
  must never depend on the managed venv. It selects a file and spawns a detached
  `peon_play.py`; it must not wait for audio.
- **A hook must never fail the agent.** Errors are logged to `event.log` and swallowed;
  `main()` exits 0 even on an unhandled exception.
- **Follow the spec, don't extend it.** Category names come from `config.ALL_CATEGORIES`
  only (CESP 1.3). A category the pack lacks is a silent skip, never an error (CESP 7.1).
- **Never trust a pack.** Manifest paths are validated against escape (`pack.resolve_sound_path`)
  and archive members against traversal (`registry._member_target`); size limits from spec
  sections 4 and 5 are enforced at install time. Magic bytes (spec 4.4) are checked at
  install *and* in `peon_play.main()`, because a hand-copied pack never met the installer —
  `pack.looks_like_audio` is the single implementation, and `doctor` scans a whole pack with it.
- **Checksums are verified once, at install**, never on the playback path. The manifest's own
  `sha256` fields are self-attested; the registry entry's `manifest_sha256` is the only digest
  that arrives out of band, so `registry.verify_manifest_digest` refuses an entry without it.
- **Linux, macOS and Windows are all first-class.** Branch on `os.name`, never assume POSIX.
  The three places that differ are `config.platform_dirs()` (XDG vs `%APPDATA%`),
  `peon_event.detached_kwargs()` (`start_new_session` vs `DETACHED_PROCESS`) and the tail of
  `peon_play.play_with_system()` (`winsound`/`MediaPlayer`). Keep each one a pure function of
  `os.name` so `tests/test_platform.py` can exercise every branch on any host.

## Hook command syntax

Hook commands must parse in `sh`, Git Bash **and** PowerShell — Claude Code picks the shell
per platform. So: no `VAR=value` prefix (the agent name is the `--agent` flag, parsed by
`peon_event.split_agent`), no `&&`, no `||`, quotes around `${CLAUDE_PLUGIN_ROOT}` only.
Windows resolves the script itself through the `py` launcher's `.py` association or the
shebang under Git Bash, so nothing may depend on an interpreter name in the command.

## Two processes, one state file

pet4agents needs a daemon because it draws a window; we don't. `play-state.json` holds the
last file and last timestamp per category, which is all no-repeat and debounce require.

## Changing the event mapping

Edit `config.EVENT_CATEGORIES`, then regenerate/adjust both hook files — a test asserts the
two sets stay identical. Claude hooks are `async: true`; Codex runs hooks synchronously and
ignores the flag, which is why its timeouts stay at 5 s.

## Audio runtime

`requirements.in` -> `requirements-uv.lock.txt` (hash-pinned, universal so one lock covers
Python 3.10 through 3.14 — numpy has no single release with wheels for both ends, hence the
marker split):

```bash
uv pip compile --universal --generate-hashes --python-version 3.10 \
    requirements.in -o requirements-uv.lock.txt
```

`venv_ready()` compares the stored state against `PLUGIN_VERSION` + the lockfile digest, so
regenerating the lock automatically forces a rebuild of every user's venv.

## Tests

`python -m pytest -q` — no audio hardware, no network, no venv. `tests/conftest.py`
rebinds the `config` module's paths into a tmp tree (they are computed at import time);
always use the `paths` fixture rather than XDG environment variables.
