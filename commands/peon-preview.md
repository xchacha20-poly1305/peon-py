---
description: Play the installed pack's CESP preview file. Usage:/peon-preview [pack-name]
argument-hint: [pack-name]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py preview:*)
---

Play the manifest `preview` of pack `$1` (defaults to the active pack when empty). This is
the browsing clip, not a category sound; if the pack has no preview the command reports
that and plays nothing.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/peon_event.py preview "$1"
```
