---
description: Switch the active sound pack. Usage:/peon-set <pack-name>
argument-hint: <pack-name>
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py set:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py list)
---

The user wants to switch to sound pack: `$1`

If `$1` is empty, list the installed packs instead and tell them the usage is `/peon-set <pack-name>`:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py list
```

Otherwise run this exactly once and report the output:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py set "$1"
```
