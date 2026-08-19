---
description: Play one sound now to check the setup. Usage:/peon-test [cesp-category]
argument-hint: [session.start|task.complete|task.error|...]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py test:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py doctor)
---

Play a sound for CESP category `$1` (defaults to `session.start` when empty):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py test "$1"
```

If nothing plays or the command reports an error, run the diagnostics and report them:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py doctor
```
