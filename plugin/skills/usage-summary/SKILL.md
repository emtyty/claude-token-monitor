---
name: usage-summary
description: "Show Claude Code token usage summary: total cost, model breakdown, daily trend, cache efficiency. Accepts optional project filter. Use when user types /usage-summary or asks about token usage, spend, or costs."
disable-model-invocation: true
---

# Usage Summary

`$ARGUMENTS` is the optional project name (e.g. `/token-monitor:usage-summary composer`).

**If `$ARGUMENTS` is provided**, run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/monitor.py" report --format txt --output - --project "$ARGUMENTS"
```

**Otherwise**, run all three:

```bash
python "${CLAUDE_PLUGIN_ROOT}/monitor.py" summary
python "${CLAUDE_PLUGIN_ROOT}/monitor.py" daily --days 7
python "${CLAUDE_PLUGIN_ROOT}/monitor.py" budget
```

After running, report:
- Sessions, API calls, total cost
- Top cost model and its cache hit rate (green ≥70%, yellow 40–70%, red <40%)
- Today's spend vs yesterday; spike days in the 7-day window
- Budget status if limits are set
