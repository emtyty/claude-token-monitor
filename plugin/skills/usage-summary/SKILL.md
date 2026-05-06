---
name: usage-summary
description: "Show Claude Code token usage. Accepts optional subcommand or project name. Use when user types /usage-summary or asks about token usage, spend, costs, cache, budget, heatmap, calendar, sessions, projects, trends, or efficiency suggestions."
disable-model-invocation: true
---

# Usage Summary

`$ARGUMENTS` is an optional subcommand or project name.

## Routing

Parse `$ARGUMENTS` to decide which command to run:

| `$ARGUMENTS` pattern | Command to run |
|---|---|
| _(empty)_ | `report --output -` |
| `daily [N]` | `daily --days <N or 30>` |
| `weekly [N]` | `weekly --weeks <N or 8>` |
| `projects [N]` | `projects --top <N or 15>` |
| `sessions [N]` | `sessions --top <N or 15>` |
| `heatmap [cost\|calls\|tokens]` | `heatmap --metric <metric or cost>` |
| `calendar [YYYY]` | `calendar --year <YYYY or current year>` |
| `cache [N]` | `cache --top <N or 15>` |
| `budget [--daily D] [--monthly M]` | `budget` (pass through any dollar args) |
| `suggest` | `suggest` |
| `activity [N]` | `activity --days <N or 30>` |
| `trend <name>` | `trend <name>` |
| `live` | `live` |
| `export [csv\|json]` | `export --format <fmt or csv>` |
| `report [html\|svg\|txt] [path]` | `report --format <fmt> -o <path>` |
| _anything else_ | `report --output - --project "$ARGUMENTS"` (treat as project filter) |

## Run

All commands use:
```bash
python "${CLAUDE_PLUGIN_ROOT}/../monitor.py" <command> [options]
```

(Single source of truth lives at the repo root — `plugin/` previously
shipped a duplicate that drifted; the duplicate has been removed.)

## After running, report

- **summary/report**: sessions, calls, total cost; top model + cache hit rate (green ≥70%, yellow 40–70%, red <40%); today vs yesterday spend; spike days; efficiency suggestions
- **daily/weekly**: date range shown, total, any spike days (>2× median)
- **projects/sessions**: top entries ranked by cost, % share
- **heatmap/calendar**: describe peak hours/days
- **cache**: overall hit rate, top savers
- **budget**: spent vs limit, % used, status
- **suggest**: list suggestions by severity with est. savings
- **trend**: daily cost trend for the project, slope (rising/flat/falling)
- **activity**: sessions and projects active per day
