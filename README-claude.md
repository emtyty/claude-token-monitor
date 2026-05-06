# token-monitor — Claude Code Plugin

Track Claude Code token spend, cache efficiency, and daily trends inside Claude Code.

No API calls. No daemon. Parses the JSONL logs Claude Code already writes to `~/.claude/projects/`.

---

## Install

```bash
pip install rich>=13.0.0
```

Requires Python 3.10+.

---

## Usage

```
/usage-summary [subcommand or project]
```

![alt text](image.png)

| Invocation | Runs |
|---|---|
| `/usage-summary` | Full dashboard (models, daily, projects, heatmap, suggestions) |
| `/usage-summary composer` | Project-filtered report |
| `/usage-summary daily 7` | `daily --days 7` |
| `/usage-summary weekly` | `weekly --weeks 8` |
| `/usage-summary projects 10` | `projects --top 10` |
| `/usage-summary sessions` | `sessions --top 15` |
| `/usage-summary heatmap calls` | `heatmap --metric calls` |
| `/usage-summary calendar 2026` | `calendar --year 2026` |
| `/usage-summary cache` | `cache --top 15` |
| `/usage-summary budget` | `budget` |
| `/usage-summary suggest` | `suggest` |
| `/usage-summary trend zero/ops` | `trend zero/ops` |
| `/usage-summary activity 14` | `activity --days 14` |
| `/usage-summary export json` | `export --format json` |

Any unrecognized argument is treated as a project name filter.

---

## Efficiency Suggestions

The full dashboard and `suggest` subcommand flag cost patterns:

| Rule | What it catches |
|---|---|
| `opus-heavy-project` | Projects where Opus handles routine edits — switch to Sonnet |
| `opus-routine-session` | Long all-Opus sessions with small outputs — likely routine |
| `low-cache-hit` | Spendy projects with cache hit rate <40% |
| `raw-input-spike` | ≥3 calls/project with raw input ≥50K tokens (log/diff dumps) |
| `day-spike` | Days costing ≥3× the 30-day median |
| `session-fragmentation` | ≥3 short sessions per (project, day) — cache rebuilt repeatedly |
| `cache-rebuild` | Sessions with cache-write/read ratio ≥0.2 (healthy <0.1) |
| `many-reads` | ≥30 Reads/session on ast-graph-supported langs |
| `explore-on-opus` | Opus sessions where ≥85% of tools are Read/Grep/Glob etc. |
| `plan-mode-opus` | Plan-mode sessions where the plan window was scan-dominated (Opus stays for synthesis; ast-graph replaces the spelunking) |
| `large-context` | Sessions whose peak call approaches the model's context cap (warn 75%, alert 90%) |
| `expensive-single-call` | Any individual API call >$5 (≥$10 → high severity) |
| `cache-cold-session` | Per-session cache hit rate <30% with ≥5 calls and ≥$2 cost |

Savings estimates are directional (Opus→Sonnet ≈ 80% cheaper).

---

## Budget hook

```json
{
  "hooks": {
    "SessionEnd": [
      { "command": "python ${CLAUDE_PLUGIN_ROOT}/monitor.py budget --daily 30 --monthly 500" }
    ]
  }
}
```

`--strict` exit codes: `0` under warn · `2` approaching limit · `1` over limit.
