---
description: Get or set the sound volume (0.0 - 1.0). Usage:/peon-volume [0.0-1.0]
argument-hint: [0.0-1.0]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py volume:*)
---

Run this command exactly once and report the output. With no argument it prints the
current volume; with an argument it sets it.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py volume "$1"
```
