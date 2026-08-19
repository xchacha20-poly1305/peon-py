---
description: Mute or unmute all sounds. Usage:/peon-mute [on|off]
argument-hint: [on|off]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py mute:*)
---

Run this command exactly once and report the output. With no argument it toggles mute.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py mute "$1"
```
