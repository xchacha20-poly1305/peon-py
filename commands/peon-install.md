---
description: Install an OpenPeon (CESP) sound pack from the registry. Usage:/peon-install <pack-name>
argument-hint: <pack-name>
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py install:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py search:*)
---

The user wants to install the sound pack: `$1`

If `$1` is empty, show what the registry offers and stop:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py search
```

Otherwise run this exactly once and report the output:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py install "$1"
```

If the pack is not found, search for a near match with
`${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py search "$1"` and suggest the closest names.
