---
description: Show peon-py status — active pack, volume, categories, audio runtime.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py doctor)
---

Run this command exactly once and report the output, then point out anything that looks
wrong (no packs installed, muted, or an audio runtime that is still building):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py doctor
```
