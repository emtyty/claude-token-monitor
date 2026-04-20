# MonitorTokenUsage

A single-file Python CLI that reads Claude Code's session JSONL logs from
`~/.claude/projects/` and reports token usage, estimated costs, trends,
and budget status.

No API calls, no daemon, no database — just parses the logs Claude Code
already writes locally.

## Install

```bash
pip install -r requirements.txt
```

Only dependency: `rich` (for tables, colours, live mode). The script
also runs without `rich` with a plain-text fallback.

Requires Python 3.10+ (uses PEP 604 `X | Y` type hints and `str.removeprefix`-era stdlib).

## Usage

```bash
python monitor.py <command> [options]
```

### Commands

| Command | Purpose |
|---|---|
| `summary` | Grand totals and per-model breakdown |
| `daily [--days N]` | Daily breakdown (default 14 days) |
| `projects [--top N]` | Top projects by cost (default 20) |
| `sessions [--top N]` | Top sessions by cost |
| `weekly [--weeks N]` | Per-ISO-week totals (Mon–Sun buckets) |
| `heatmap [--metric cost\|calls\|tokens]` | 7×24 day-of-week × hour heatmap (local time) |
| `calendar [--year YYYY] [--metric cost\|calls]` | GitHub-style yearly activity grid |
| `trend <project> [--days N]` | Daily trend for one project (substring match) |
| `activity [--days N]` | Per-day unique sessions & projects active + top project of each day |
| `cache [--top N]` | Cache hit rate + estimated savings per project |
| `budget [--daily USD] [--monthly USD] [--warn-at 0.8] [--strict]` | Today + MTD spend vs limits |
| `live [--interval S]` | Auto-refreshing dashboard |
| `export --format csv\|json [-o path]` | Raw per-call records |
| `report --format html\|svg\|txt [-o path]` | Full dashboard export (for archive / print-to-PDF) |

### Examples

```bash
# What have I spent?
python monitor.py summary

# Last week, day by day
python monitor.py daily --days 7

# Which projects cost the most?
python monitor.py projects --top 10

# Trend for one project (substring of path)
python monitor.py trend ZeroCTX

# When do I use Claude the most?
python monitor.py heatmap
python monitor.py heatmap --metric calls

# How many sessions / projects did I juggle per day?
python monitor.py activity --days 30

# Am I over budget?
python monitor.py budget --daily 30 --monthly 500

# CI / cron guard — exit 1 if over, 2 if above warn threshold
python monitor.py budget --monthly 500 --strict

# Live dashboard while coding
python monitor.py live --interval 3

# Week-level view
python monitor.py weekly --weeks 8

# GitHub-style calendar for a year
python monitor.py calendar --year 2026

# Cache efficiency — how much did caching save you?
python monitor.py cache

# Export full dashboard to HTML (then browser Print -> Save as PDF)
python monitor.py report --format html -o usage-report.html

# Or archive as SVG (color-accurate, scales cleanly)
python monitor.py report --format svg -o usage-report.svg

# Export raw per-call records to CSV
python monitor.py export --format csv -o usage.csv
```

## PDF output

There is no dedicated PDF command — adding a PDF-rendering library
(WeasyPrint, ReportLab) would balloon the dependency footprint for a
one-file tool. Instead:

1. `python monitor.py report --format html -o report.html`
2. Open the HTML file in a browser.
3. Use **File > Print > Save as PDF** (Chrome, Edge, Firefox all support this).

This is typically sharper than library-rendered PDF, and needs no extra install.

## Pricing

Per-1M-token rates live in a dict at the top of [monitor.py](monitor.py)
(`PRICING`). Models are matched by substring against the `model` field
in each JSONL entry (e.g. `claude-opus-4-6` matches the `claude-opus-4`
entry). Unknown models fall back to Sonnet-equivalent pricing.

Edit the dict if your rates differ (enterprise, batch tier, etc.).

## How it works

Claude Code logs every session to `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`.
Each line is one event; assistant messages carry a `message.usage` block:

```json
{
  "type": "assistant",
  "sessionId": "...",
  "timestamp": "2026-04-15T07:59:30.447Z",
  "message": {
    "model": "claude-opus-4-6",
    "id": "msg_...",
    "usage": {
      "input_tokens": 3,
      "output_tokens": 653,
      "cache_read_input_tokens": 12329,
      "cache_creation_input_tokens": 7199
    }
  }
}
```

The tool walks every `.jsonl` file, deduplicates by `(sessionId, message.id)`
— because one assistant turn with multiple content blocks logs multiple
lines that carry the same (full) usage block — and aggregates from there.

## Budget alerts in practice

`budget --strict` is the scripting-friendly mode. Exit codes:

- `0` — under warn threshold
- `2` — over warn threshold (default 80% of limit)
- `1` — over limit

Drop it into a cron / scheduled task:

```bash
# Every hour: warn if I'm close to blowing this month's budget
python /path/to/monitor.py budget --monthly 500 --strict \
    || notify-send "Claude Code approaching budget"
```

Or a Claude Code `SessionEnd` hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionEnd": [
      { "command": "python C:/path/MonitorTokenUsage/monitor.py budget --daily 30" }
    ]
  }
}
```

## Notes

- **Timestamps are UTC in the logs.** Heatmap and budget convert to local
  time via `datetime.astimezone()`. `daily` / `trend` bucket by the
  local date for the same reason.
- **`<synthetic>` model entries** are Claude Code's internal zero-token
  messages — they show up in `summary` with `$0` cost.
- **Windows consoles** default to cp1252; the script reconfigures stdout
  to UTF-8 on startup so rich's box-drawing and glyphs render.
- **No network.** All data is local. The tool never calls the Anthropic
  API or sends your logs anywhere.

## License

MIT — see [LICENSE](LICENSE).
