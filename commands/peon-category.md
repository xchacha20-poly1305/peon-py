---
description: List or toggle a CESP sound category. Usage:/peon-category [name [on|off]]
argument-hint: [session.start|task.complete|task.progress|... [on|off]]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py category:*)
---

Run this command exactly once and report the output. With no argument it lists the nine
CESP categories and whether each is on. With a name and `on` or `off` it toggles that one.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py category "$1" "$2"
```
