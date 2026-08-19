# Privacy

peon-py plays local audio files. It has no analytics, no telemetry and no account.

## What leaves your machine

Nothing, except when you explicitly install a pack (`/peon-install`, `/peon-search`):

- `https://peonping.github.io/registry/index.json` — the public pack registry index.
- `https://github.com/<repo>/archive/refs/tags/<ref>.tar.gz` — the pack you asked for.

Both are plain HTTPS GETs with a `peon-py/<version>` User-Agent. No identifiers, no
session data, no prompt content is sent. GitHub and GitHub Pages see your IP address, as
they would for any download.

## What is stored locally

| Path | Contents |
|---|---|
| `~/.config/peon-py/config.json` | Active pack, volume, mute state, per-category toggles |
| `~/.local/state/peon-py/play-state.json` | Last sound and timestamp per category (for no-repeat and debounce) |
| `~/.local/state/peon-py/event.log` | Diagnostics: skipped events, pack errors. Truncated at 5 MB |
| `~/.local/state/peon-py/install.log` | Output of the audio runtime build |
| `~/.local/share/peon-py/venv/` | The managed audio virtualenv |
| `~/.openpeon/packs/` | Installed sound packs |

Hook payloads are read from stdin and discarded — prompt text, tool inputs and file
contents are never stored or logged. Only the event *name* influences behaviour.

## What runs

Hooks run `scripts/peon_event.py`, which spawns `scripts/peon_play.py` (and, on first use,
a background installer for the audio runtime). Installed packs are data only: audio files
and a JSON manifest, validated against the CESP size and magic-byte rules before they are
accepted, and never executed.

Delete everything with:

```bash
rm -rf ~/.config/peon-py ~/.local/state/peon-py ~/.local/share/peon-py ~/.openpeon/packs
```
