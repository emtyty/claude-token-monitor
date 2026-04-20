#!/usr/bin/env python3
"""Claude Code token usage monitor.

Reads session JSONL logs from ~/.claude/projects/ and reports token
usage, estimated costs, and trends. Commands: summary, daily, projects,
sessions, export, live.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator

# Windows consoles often default to cp1252; force UTF-8 so rich's
# ellipsis and box-drawing characters render correctly.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    RICH = True
except ImportError:  # graceful fallback
    RICH = False


# Pricing per 1M tokens (USD). Kept in one place so it's easy to tune.
# Matched by substring against the model name reported in the JSONL.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4":     {"in": 15.0, "out": 75.0, "cr": 1.50, "cw": 18.75},
    "claude-sonnet-4":   {"in":  3.0, "out": 15.0, "cr": 0.30, "cw":  3.75},
    "claude-haiku-4":    {"in":  1.0, "out":  5.0, "cr": 0.10, "cw":  1.25},
    "claude-3-5-sonnet": {"in":  3.0, "out": 15.0, "cr": 0.30, "cw":  3.75},
    "claude-3-5-haiku":  {"in":  0.8, "out":  4.0, "cr": 0.08, "cw":  1.00},
    "claude-3-opus":     {"in": 15.0, "out": 75.0, "cr": 1.50, "cw": 18.75},
    "claude-3-haiku":    {"in": 0.25, "out": 1.25, "cr": 0.03, "cw":  0.30},
}
DEFAULT_PRICE = {"in": 3.0, "out": 15.0, "cr": 0.30, "cw": 3.75}


def model_price(model: str) -> dict[str, float]:
    m = (model or "").lower()
    for prefix, price in PRICING.items():
        if prefix in m:
            return price
    return DEFAULT_PRICE


def calc_cost(usage: dict, model: str) -> float:
    p = model_price(model)
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    return (
        inp * p["in"]  / 1_000_000
        + out * p["out"] / 1_000_000
        + cr  * p["cr"]  / 1_000_000
        + cw  * p["cw"]  / 1_000_000
    )


def projects_root() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
    return home / ".claude" / "projects"


def decode_project(folder: str) -> str:
    # Claude Code encodes "C:\Users\foo\bar" as "c--Users-foo-bar".
    if len(folder) >= 3 and folder[1:3] == "--":
        return folder[0].upper() + ":/" + folder[3:].replace("-", "/")
    return folder.replace("-", "/")


def shorten_path(path: str) -> str:
    """Replace the user home prefix with ~ for display."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if not home:
        return path
    norm_home = home.replace("\\", "/").rstrip("/")
    if path.lower().startswith(norm_home.lower()):
        return "~" + path[len(norm_home):]
    return path


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Record:
    project: str
    session_id: str
    timestamp: str
    model: str
    usage: dict
    cost: float
    cwd: str
    msg_id: str


def iter_records(root: Path) -> Iterator[Record]:
    """Yield one Record per distinct assistant API call.

    Multiple JSONL entries share the same message.id when an assistant
    turn has several content blocks. Their `usage` block is the full
    per-call total and is duplicated — so we dedupe by (session, msg_id).
    """
    if not root.exists():
        return
    seen: set[tuple[str, str]] = set()
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                f = jsonl.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with f:
                for line in f:
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("type") != "assistant":
                        continue
                    msg = e.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    msg_id = msg.get("id") or ""
                    session_id = e.get("sessionId") or ""
                    key = (session_id, msg_id)
                    if msg_id and key in seen:
                        continue
                    seen.add(key)
                    model = msg.get("model") or "unknown"
                    yield Record(
                        project=project_dir.name,
                        session_id=session_id,
                        timestamp=e.get("timestamp") or "",
                        model=model,
                        usage=usage,
                        cost=calc_cost(usage, model),
                        cwd=e.get("cwd") or "",
                        msg_id=msg_id,
                    )


def empty_agg() -> dict:
    return {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "calls": 0, "last": None}


def aggregate(records: Iterable[Record], key_fn: Callable[[Record], str | None]) -> dict[str, dict]:
    agg: dict[str, dict] = defaultdict(empty_agg)
    for r in records:
        k = key_fn(r)
        if k is None:
            continue
        a = agg[k]
        u = r.usage
        a["in"]  += int(u.get("input_tokens") or 0)
        a["out"] += int(u.get("output_tokens") or 0)
        a["cr"]  += int(u.get("cache_read_input_tokens") or 0)
        a["cw"]  += int(u.get("cache_creation_input_tokens") or 0)
        a["cost"] += r.cost
        a["calls"] += 1
        ts = parse_ts(r.timestamp)
        if ts and (a["last"] is None or ts > a["last"]):
            a["last"] = ts
    return agg


def fmt_num(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(int(n))


def fmt_cost(c: float) -> str:
    if c >= 1:
        return f"${c:,.2f}"
    if c == 0:
        return "$0"
    return f"${c:.4f}"


def load_all() -> list[Record]:
    return list(iter_records(projects_root()))


# ------------------------------- Commands ------------------------------- #


def cmd_summary(args) -> None:
    records = load_all()
    if not records:
        print("No usage records found in", projects_root())
        return

    total = empty_agg()
    for r in records:
        u = r.usage
        total["in"]   += int(u.get("input_tokens") or 0)
        total["out"]  += int(u.get("output_tokens") or 0)
        total["cr"]   += int(u.get("cache_read_input_tokens") or 0)
        total["cw"]   += int(u.get("cache_creation_input_tokens") or 0)
        total["cost"] += r.cost
        total["calls"] += 1

    by_model = aggregate(records, lambda r: r.model)
    sessions = {r.session_id for r in records if r.session_id}

    if not RICH:
        print(f"Sessions: {len(sessions)}   API calls: {total['calls']}")
        print(f"Input:  {fmt_num(total['in'])}   Output: {fmt_num(total['out'])}")
        print(f"Cache R:{fmt_num(total['cr'])}   Cache W:{fmt_num(total['cw'])}")
        print(f"Cost:   {fmt_cost(total['cost'])}")
        return

    console = Console()
    console.rule("[bold]Claude Code Token Usage[/bold]")
    console.print(f"Sessions: [cyan]{len(sessions)}[/cyan]   "
                  f"API calls: [cyan]{total['calls']}[/cyan]   "
                  f"Est. cost: [green]{fmt_cost(total['cost'])}[/green]")

    t = Table(title="Totals", box=box.SIMPLE_HEAVY, show_header=True)
    t.add_column("Metric"); t.add_column("Tokens", justify="right"); t.add_column("% of tokens", justify="right")
    grand = total["in"] + total["out"] + total["cr"] + total["cw"]
    def pct(n: int) -> str:
        return f"{(n / grand * 100):.1f}%" if grand else "0%"
    t.add_row("Input",       fmt_num(total["in"]),  pct(total["in"]))
    t.add_row("Output",      fmt_num(total["out"]), pct(total["out"]))
    t.add_row("Cache read",  fmt_num(total["cr"]),  pct(total["cr"]))
    t.add_row("Cache write", fmt_num(total["cw"]),  pct(total["cw"]))
    console.print(t)

    t = Table(title="By Model", box=box.SIMPLE_HEAVY)
    t.add_column("Model")
    t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right")
    t.add_column("Output", justify="right")
    t.add_column("Cache R", justify="right")
    t.add_column("Cache W", justify="right")
    t.add_column("Cost", justify="right")
    for model, a in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        t.add_row(
            model, str(a["calls"]),
            fmt_num(a["in"]), fmt_num(a["out"]),
            fmt_num(a["cr"]), fmt_num(a["cw"]),
            fmt_cost(a["cost"]),
        )
    console.print(t)


def cmd_daily(args) -> None:
    records = load_all()
    by_day = aggregate(
        records,
        lambda r: parse_ts(r.timestamp).date().isoformat() if parse_ts(r.timestamp) else None,
    )
    days = sorted(by_day.keys(), reverse=True)[: args.days]
    if not days:
        print("No dated records found.")
        return

    if not RICH:
        for d in days:
            a = by_day[d]
            print(f"{d}  calls={a['calls']:4d}  in={fmt_num(a['in']):>8s}  "
                  f"out={fmt_num(a['out']):>8s}  cost={fmt_cost(a['cost'])}")
        return

    console = Console()
    t = Table(title=f"Daily Usage (last {len(days)})", box=box.SIMPLE_HEAVY)
    t.add_column("Date"); t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right"); t.add_column("Output", justify="right")
    t.add_column("Cache R", justify="right"); t.add_column("Cache W", justify="right")
    t.add_column("Cost", justify="right")
    total_cost = 0.0
    for d in days:
        a = by_day[d]
        total_cost += a["cost"]
        t.add_row(d, str(a["calls"]),
                  fmt_num(a["in"]), fmt_num(a["out"]),
                  fmt_num(a["cr"]), fmt_num(a["cw"]),
                  fmt_cost(a["cost"]))
    console.print(t)
    console.print(f"[bold]Total for window:[/bold] [green]{fmt_cost(total_cost)}[/green]")


def cmd_projects(args) -> None:
    records = load_all()
    by_project = aggregate(records, lambda r: r.project)
    if not by_project:
        print("No records.")
        return

    if not RICH:
        for project, a in sorted(by_project.items(), key=lambda kv: -kv[1]["cost"])[: args.top]:
            print(f"{shorten_path(decode_project(project)):60s}  calls={a['calls']:4d}  cost={fmt_cost(a['cost'])}")
        return

    console = Console()
    t = Table(title=f"Top {args.top} Projects by Cost", box=box.SIMPLE_HEAVY)
    t.add_column("Project"); t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right"); t.add_column("Output", justify="right")
    t.add_column("Cost", justify="right"); t.add_column("Last active", justify="right")
    for project, a in sorted(by_project.items(), key=lambda kv: -kv[1]["cost"])[: args.top]:
        short = shorten_path(decode_project(project))
        if len(short) > 55:
            short = "..." + short[-52:]
        last = a["last"].strftime("%Y-%m-%d") if a["last"] else "-"
        t.add_row(short, str(a["calls"]),
                  fmt_num(a["in"]), fmt_num(a["out"]),
                  fmt_cost(a["cost"]), last)
    console.print(t)


def cmd_sessions(args) -> None:
    records = load_all()
    by_session = aggregate(records, lambda r: r.session_id)
    if not by_session:
        print("No records.")
        return

    console = Console() if RICH else None
    rows = sorted(by_session.items(), key=lambda kv: -kv[1]["cost"])[: args.top]
    if not RICH:
        for sess, a in rows:
            print(f"{sess[:8]}...  calls={a['calls']:4d}  cost={fmt_cost(a['cost'])}")
        return

    t = Table(title=f"Top {args.top} Sessions by Cost", box=box.SIMPLE_HEAVY)
    t.add_column("Session"); t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right"); t.add_column("Output", justify="right")
    t.add_column("Cache R", justify="right"); t.add_column("Cache W", justify="right")
    t.add_column("Cost", justify="right"); t.add_column("Last active", justify="right")
    for sess, a in rows:
        last = a["last"].strftime("%Y-%m-%d %H:%M") if a["last"] else "-"
        t.add_row(sess[:8] + "...", str(a["calls"]),
                  fmt_num(a["in"]), fmt_num(a["out"]),
                  fmt_num(a["cr"]), fmt_num(a["cw"]),
                  fmt_cost(a["cost"]), last)
    console.print(t)


def cmd_export(args) -> None:
    records = load_all()
    if args.output == "-":
        out = sys.stdout
        close = False
    else:
        out = open(args.output, "w", encoding="utf-8", newline="")
        close = True
    try:
        if args.format == "csv":
            w = csv.writer(out)
            w.writerow([
                "timestamp", "project", "session_id", "model",
                "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_tokens",
                "cost_usd",
            ])
            for r in records:
                u = r.usage
                w.writerow([
                    r.timestamp, decode_project(r.project), r.session_id, r.model,
                    int(u.get("input_tokens") or 0),
                    int(u.get("output_tokens") or 0),
                    int(u.get("cache_read_input_tokens") or 0),
                    int(u.get("cache_creation_input_tokens") or 0),
                    f"{r.cost:.6f}",
                ])
        else:
            json.dump([
                {
                    "timestamp": r.timestamp,
                    "project": decode_project(r.project),
                    "session_id": r.session_id,
                    "model": r.model,
                    "usage": r.usage,
                    "cost_usd": round(r.cost, 6),
                }
                for r in records
            ], out, indent=2)
    finally:
        if close:
            out.close()
    if close:
        print(f"Wrote {len(records)} records -> {args.output}", file=sys.stderr)


def cmd_live(args) -> None:
    if not RICH:
        print("Live mode requires `pip install rich`.", file=sys.stderr)
        sys.exit(1)
    console = Console()

    def render() -> Table:
        records = load_all()
        today_iso = date.today().isoformat()
        yday_iso = (date.today() - timedelta(days=1)).isoformat()
        by_day = aggregate(
            records,
            lambda r: parse_ts(r.timestamp).date().isoformat() if parse_ts(r.timestamp) else None,
        )
        today = by_day.get(today_iso, empty_agg())
        yday = by_day.get(yday_iso, empty_agg())
        total = empty_agg()
        for a in by_day.values():
            for k in ("in", "out", "cr", "cw", "calls"):
                total[k] += a[k]
            total["cost"] += a["cost"]

        t = Table(
            title=f"Claude Code Live Monitor  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        t.add_column("Scope"); t.add_column("Calls", justify="right")
        t.add_column("Input", justify="right"); t.add_column("Output", justify="right")
        t.add_column("Cache R", justify="right"); t.add_column("Cache W", justify="right")
        t.add_column("Cost", justify="right")
        for label, a, style in [
            ("Today", today, "cyan"),
            ("Yesterday", yday, "dim"),
            ("All-time", total, "bold green"),
        ]:
            t.add_row(
                f"[{style}]{label}[/{style}]", str(a["calls"]),
                fmt_num(a["in"]), fmt_num(a["out"]),
                fmt_num(a["cr"]), fmt_num(a["cw"]),
                f"[{style}]{fmt_cost(a['cost'])}[/{style}]",
            )
        return t

    with Live(render(), refresh_per_second=2, console=console, screen=False) as live:
        try:
            while True:
                time.sleep(max(1.0, args.interval))
                live.update(render())
        except KeyboardInterrupt:
            pass


# -------------------------- Heatmap / trend / budget ------------------------- #


_HEAT_STEPS = [
    # (threshold_fraction, color, glyph)
    (0.00, "grey23",     "·"),
    (0.02, "grey50",     "░"),
    (0.10, "cyan",       "▒"),
    (0.25, "blue",       "▓"),
    (0.45, "green",      "▓"),
    (0.65, "yellow",     "█"),
    (0.80, "orange3",    "█"),
    (0.92, "red",        "█"),
]


def _heat_cell(frac: float) -> str:
    step = _HEAT_STEPS[0]
    for s in _HEAT_STEPS:
        if frac >= s[0]:
            step = s
    color, glyph = step[1], step[2]
    return f"[{color}]{glyph}[/]"


def cmd_heatmap(args) -> None:
    """Day-of-week × hour-of-day heatmap of usage (local time)."""
    if not RICH:
        print("Heatmap requires `pip install rich`.", file=sys.stderr)
        sys.exit(1)
    records = load_all()
    metric = args.metric  # "cost" | "calls" | "tokens"

    # grid[dow 0=Mon..6=Sun][hour 0..23] = float
    grid: list[list[float]] = [[0.0] * 24 for _ in range(7)]
    totals_by_dow = [0.0] * 7
    totals_by_hour = [0.0] * 24
    counted = 0
    for r in records:
        ts = parse_ts(r.timestamp)
        if ts is None:
            continue
        local = ts.astimezone()  # convert UTC -> local
        dow = local.weekday()
        hour = local.hour
        if metric == "cost":
            val = r.cost
        elif metric == "calls":
            val = 1.0
        else:  # tokens: sum of all usage token fields
            u = r.usage
            val = float(
                int(u.get("input_tokens") or 0)
                + int(u.get("output_tokens") or 0)
                + int(u.get("cache_read_input_tokens") or 0)
                + int(u.get("cache_creation_input_tokens") or 0)
            )
        grid[dow][hour] += val
        totals_by_dow[dow] += val
        totals_by_hour[hour] += val
        counted += 1

    if counted == 0:
        print("No dated records to display.")
        return

    peak = max(max(row) for row in grid) or 1.0
    console = Console()
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def fmt_total(v: float) -> str:
        if metric == "cost":
            return fmt_cost(v)
        if metric == "calls":
            return str(int(v))
        return fmt_num(int(v))

    # Axis header: two rows of digits so we don't eat horizontal space
    hour_tens = "".join(f"{h//10 if h % 5 == 0 else ' '}" for h in range(24))
    hour_ones = "".join(str(h % 10) for h in range(24))

    console.print(
        f"[bold]Usage Heatmap[/bold] ({metric}, local time)  "
        f"peak cell: [cyan]{fmt_total(peak)}[/cyan]   "
        f"grand total: [green]{fmt_total(sum(totals_by_dow))}[/green]"
    )
    console.print(f"       {hour_tens}   Total")
    console.print(f"       {hour_ones}")
    for dow in range(7):
        cells = "".join(_heat_cell(grid[dow][h] / peak) for h in range(24))
        console.print(f" [bold]{dow_labels[dow]}[/bold]   {cells}   {fmt_total(totals_by_dow[dow]):>8s}")
    # column totals line
    col_totals = "".join(
        _heat_cell(totals_by_hour[h] / (max(totals_by_hour) or 1.0)) for h in range(24)
    )
    console.print(f" [dim]Σh[/dim]   {col_totals}")
    # legend
    legend = "  ".join(
        f"{_heat_cell(step[0] + 0.01)} {int(step[0]*100)}%+"
        for step in _HEAT_STEPS
    )
    console.print(f"\n Legend: {legend}")


def cmd_trend(args) -> None:
    """Daily cost/token trend for a single project (substring match)."""
    records = load_all()
    q = args.project.lower()
    matches: list[Record] = [
        r for r in records
        if q in decode_project(r.project).lower() or q in (r.cwd or "").lower()
    ]
    if not matches:
        print(f"No records match project query: {args.project!r}")
        print("Try `monitor projects` to see available projects.")
        sys.exit(1)

    # identify the matched project (most common decoded path)
    label_counts: dict[str, int] = defaultdict(int)
    for r in matches:
        label_counts[decode_project(r.project)] += 1
    primary = max(label_counts.items(), key=lambda kv: kv[1])[0]

    by_day = aggregate(
        matches,
        lambda r: parse_ts(r.timestamp).date().isoformat() if parse_ts(r.timestamp) else None,
    )
    days = sorted(by_day.keys())
    if args.days:
        days = days[-args.days:]

    if not days:
        print("No dated records for project.")
        return

    # sparkline of cost
    costs = [by_day[d]["cost"] for d in days]
    peak = max(costs) or 1.0
    bars = " ▁▂▃▄▅▆▇█"
    spark = "".join(bars[min(len(bars) - 1, int(c / peak * (len(bars) - 1)))] for c in costs)

    if not RICH:
        print(f"Project: {shorten_path(primary)}")
        print(f"Spark:   {spark}")
        for d in days:
            a = by_day[d]
            print(f"{d}  calls={a['calls']:4d}  cost={fmt_cost(a['cost'])}")
        return

    console = Console()
    console.print(f"[bold]Project:[/bold] {shorten_path(primary)}")
    console.print(f"[bold]Sessions:[/bold] {len({r.session_id for r in matches if r.session_id})}   "
                  f"[bold]Total spend:[/bold] [green]{fmt_cost(sum(costs))}[/green]   "
                  f"[bold]Spark:[/bold] [cyan]{spark}[/cyan]")

    t = Table(title=f"Daily Trend (last {len(days)} active days)", box=box.SIMPLE_HEAVY)
    t.add_column("Date", no_wrap=True); t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right"); t.add_column("Output", justify="right")
    t.add_column("Cache R", justify="right"); t.add_column("Cache W", justify="right")
    t.add_column("Cost", justify="right", no_wrap=True)
    t.add_column("", justify="left", no_wrap=True)
    bar_width = 12
    for d in days:
        a = by_day[d]
        bar_len = int(a["cost"] / peak * bar_width)
        bar = "█" * bar_len
        t.add_row(
            d, str(a["calls"]),
            fmt_num(a["in"]), fmt_num(a["out"]),
            fmt_num(a["cr"]), fmt_num(a["cw"]),
            fmt_cost(a["cost"]),
            f"[cyan]{bar}[/cyan]",
        )
    console.print(t)


def _iso_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def cmd_weekly(args) -> None:
    """Per-ISO-week usage (Mon-Sun)."""
    records = load_all()

    by_week: dict[str, dict] = defaultdict(empty_agg)
    week_sessions: dict[str, set] = defaultdict(set)
    week_projects: dict[str, set] = defaultdict(set)
    for r in records:
        ts = parse_ts(r.timestamp)
        if ts is None:
            continue
        ws = _iso_week_start(ts.astimezone().date()).isoformat()
        a = by_week[ws]
        u = r.usage
        a["in"]  += int(u.get("input_tokens") or 0)
        a["out"] += int(u.get("output_tokens") or 0)
        a["cr"]  += int(u.get("cache_read_input_tokens") or 0)
        a["cw"]  += int(u.get("cache_creation_input_tokens") or 0)
        a["cost"] += r.cost
        a["calls"] += 1
        if r.session_id:
            week_sessions[ws].add(r.session_id)
        if r.project:
            week_projects[ws].add(r.project)

    weeks = sorted(by_week.keys())
    if args.weeks:
        weeks = weeks[-args.weeks:]
    if not weeks:
        print("No dated records.")
        return

    if not RICH:
        for w in weeks:
            a = by_week[w]
            print(f"{w}  calls={a['calls']:5d}  cost={fmt_cost(a['cost'])}  "
                  f"sessions={len(week_sessions[w]):2d}  projects={len(week_projects[w]):2d}")
        return

    console = Console()
    costs = [by_week[w]["cost"] for w in weeks]
    peak = max(costs) or 1.0
    bar_width = 14

    t = Table(title=f"Weekly Usage (last {len(weeks)} weeks)", box=box.SIMPLE_HEAVY)
    t.add_column("Week of", no_wrap=True)
    t.add_column("Sess", justify="right")
    t.add_column("Proj", justify="right")
    t.add_column("Calls", justify="right")
    t.add_column("Input", justify="right")
    t.add_column("Output", justify="right")
    t.add_column("Cost", justify="right", no_wrap=True)
    t.add_column("", justify="left", no_wrap=True)
    for w in weeks:
        a = by_week[w]
        bar = "█" * int(a["cost"] / peak * bar_width)
        t.add_row(
            w, str(len(week_sessions[w])), str(len(week_projects[w])),
            str(a["calls"]),
            fmt_num(a["in"]), fmt_num(a["out"]),
            fmt_cost(a["cost"]),
            f"[cyan]{bar}[/cyan]",
        )
    console.print(t)
    console.print(f"\n[bold]Total for window:[/bold] [green]{fmt_cost(sum(costs))}[/green]  "
                  f"across {len({s for w in weeks for s in week_sessions[w]})} sessions")


def cmd_calendar(args) -> None:
    """GitHub-style yearly activity grid (7 rows × 53 cols)."""
    if not RICH:
        print("Calendar requires `pip install rich`.", file=sys.stderr)
        sys.exit(1)
    records = load_all()
    year = args.year or date.today().year

    day_cost: dict[date, float] = defaultdict(float)
    day_calls: dict[date, int] = defaultdict(int)
    for r in records:
        ts = parse_ts(r.timestamp)
        if ts is None:
            continue
        d = ts.astimezone().date()
        if d.year != year:
            continue
        day_cost[d] += r.cost
        day_calls[d] += 1

    if not day_cost:
        print(f"No records for year {year}.")
        return

    metric_map = day_cost if args.metric == "cost" else day_calls
    peak = max(metric_map.values()) or 1.0

    # Build a 7×53 grid. Each column is a week starting on Monday.
    jan1 = date(year, 1, 1)
    # shift so column 0 aligns with the Monday of jan1's week
    grid_start = jan1 - timedelta(days=jan1.weekday())
    console = Console()
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Month label row: show month letter at the first column of each month
    month_row = [" "] * 53
    for m in range(1, 13):
        first = date(year, m, 1)
        col = (first - grid_start).days // 7
        if 0 <= col < 53:
            month_row[col] = first.strftime("%b")[0]
    # spread 3-letter month initials every few columns? keep single letters.

    console.print(f"[bold]Activity Calendar {year}[/bold]  "
                  f"(metric: {args.metric}, peak day: "
                  f"{fmt_cost(peak) if args.metric == 'cost' else int(peak)})")
    # Month axis — align with cell columns (day-label block is 7 chars wide)
    month_axis = " " * 7 + "".join(month_row)
    console.print(f"[dim]{month_axis}[/dim]")

    for dow in range(7):
        cells = []
        for col in range(53):
            d = grid_start + timedelta(weeks=col, days=dow)
            if d.year != year or d > date.today():
                cells.append("[grey15] [/]")
                continue
            v = metric_map.get(d, 0)
            cells.append(_heat_cell(v / peak))
        console.print(f"  [dim]{dow_labels[dow]}[/dim]  {''.join(cells)}")

    # Legend + totals
    legend = "  ".join(
        f"{_heat_cell(step[0] + 0.01)} {int(step[0]*100)}%+"
        for step in _HEAT_STEPS
    )
    total_cost = sum(day_cost.values())
    console.print(f"\n  [bold]{len(day_cost)}[/bold] active days  "
                  f"[bold]{sum(day_calls.values())}[/bold] calls  "
                  f"[bold green]{fmt_cost(total_cost)}[/bold green] spent")
    console.print(f"  Legend: {legend}")


def cmd_cache(args) -> None:
    """Cache efficiency analysis: hit rate and estimated savings."""
    records = load_all()
    if not records:
        print("No records.")
        return

    def analyze(recs: list[Record]) -> dict:
        inp = out = cr = cw = 0
        cost = 0.0
        uncached_cost = 0.0
        for r in recs:
            u = r.usage
            i = int(u.get("input_tokens") or 0)
            o = int(u.get("output_tokens") or 0)
            cri = int(u.get("cache_read_input_tokens") or 0)
            cwi = int(u.get("cache_creation_input_tokens") or 0)
            inp += i; out += o; cr += cri; cw += cwi
            cost += r.cost
            # "what would it have cost without caching?" — all cache reads
            # and cache creations would have been plain input tokens.
            p = model_price(r.model)
            uncached_cost += (
                (i + cri + cwi) * p["in"] / 1_000_000
                + o * p["out"] / 1_000_000
            )
        total_input_like = inp + cr + cw
        hit_rate = (cr / total_input_like) if total_input_like else 0.0
        savings = uncached_cost - cost
        return {
            "calls": len(recs),
            "input": inp, "output": out, "cache_read": cr, "cache_write": cw,
            "cost": cost, "uncached_cost": uncached_cost,
            "savings": savings,
            "hit_rate": hit_rate,
        }

    overall = analyze(records)

    # per-project breakdown
    by_project: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        by_project[r.project].append(r)
    project_stats = {
        p: analyze(recs) for p, recs in by_project.items()
    }

    if not RICH:
        print(f"Overall cache hit rate: {overall['hit_rate']*100:.1f}%")
        print(f"Spent: {fmt_cost(overall['cost'])}  "
              f"Would have cost without caching: {fmt_cost(overall['uncached_cost'])}")
        print(f"Estimated savings: {fmt_cost(overall['savings'])}")
        return

    console = Console()
    console.print(f"[bold]Cache Efficiency[/bold]")
    console.print(f"  Hit rate:          [cyan]{overall['hit_rate']*100:.1f}%[/cyan] "
                  f"of input-side tokens came from cache")
    console.print(f"  Actual spend:      [green]{fmt_cost(overall['cost'])}[/green]")
    console.print(f"  Without caching:   [red]{fmt_cost(overall['uncached_cost'])}[/red]")
    console.print(f"  Estimated savings: [bold green]{fmt_cost(overall['savings'])}[/bold green] "
                  f"([bold]{(overall['savings']/overall['uncached_cost']*100 if overall['uncached_cost'] else 0):.0f}%[/bold])")

    t = Table(title=f"Top {args.top} Projects by Spend (cache view)", box=box.SIMPLE_HEAVY)
    t.add_column("Project", overflow="ellipsis")
    t.add_column("Calls", justify="right")
    t.add_column("Hit %", justify="right")
    t.add_column("Spent", justify="right", no_wrap=True)
    t.add_column("No-cache", justify="right", no_wrap=True)
    t.add_column("Saved", justify="right", no_wrap=True)
    for project, stats in sorted(project_stats.items(), key=lambda kv: -kv[1]["cost"])[: args.top]:
        short = shorten_path(decode_project(project))
        hr = stats["hit_rate"] * 100
        hr_color = "green" if hr >= 80 else "yellow" if hr >= 50 else "red"
        t.add_row(
            short, str(stats["calls"]),
            f"[{hr_color}]{hr:.0f}%[/{hr_color}]",
            fmt_cost(stats["cost"]),
            fmt_cost(stats["uncached_cost"]),
            f"[green]{fmt_cost(stats['savings'])}[/green]",
        )
    console.print(t)


def _cost_markup(c: float, peak: float) -> str:
    frac = c / peak if peak else 0
    color = "green" if frac < 0.33 else "yellow" if frac < 0.66 else "red"
    return f"[{color}]{fmt_cost(c)}[/{color}]"


def cmd_report(args) -> None:
    """Export a comprehensive dashboard to HTML or SVG."""
    if not RICH:
        print("Report export requires `pip install rich`.", file=sys.stderr)
        sys.exit(1)
    width = args.width
    console = Console(record=True, width=width, force_terminal=True, color_system="truecolor")

    records = load_all()

    # Optional project filter
    project_label: str | None = None
    if getattr(args, "project", None):
        q = args.project.lower()
        records = [
            r for r in records
            if q in decode_project(r.project).lower()
            or q in r.project.lower()
            or q in (r.cwd or "").lower().replace("\\", "/")
        ]
        if not records:
            print(f"No records match project: {args.project!r}", file=sys.stderr)
            sys.exit(1)
        label_counts: dict[str, int] = defaultdict(int)
        for r in records:
            label_counts[decode_project(r.project)] += 1
        project_label = shorten_path(max(label_counts.items(), key=lambda kv: kv[1])[0])

    if not records:
        console.print("No records to report.")
    else:
        title_suffix = f" — {project_label}" if project_label else ""
        console.rule(f"[bold]Claude Code Usage Report{title_suffix} — {datetime.now():%Y-%m-%d %H:%M}[/bold]")

        # Section 1: overview
        total = empty_agg()
        for r in records:
            u = r.usage
            total["in"]   += int(u.get("input_tokens") or 0)
            total["out"]  += int(u.get("output_tokens") or 0)
            total["cr"]   += int(u.get("cache_read_input_tokens") or 0)
            total["cw"]   += int(u.get("cache_creation_input_tokens") or 0)
            total["cost"] += r.cost
            total["calls"] += 1
        sessions = {r.session_id for r in records if r.session_id}
        projects_set = {r.project for r in records if r.project}
        total_input_like = total["in"] + total["cr"] + total["cw"]
        cache_hit_rate = total["cr"] / total_input_like if total_input_like else 0.0
        hit_color = "green" if cache_hit_rate >= 0.7 else "yellow" if cache_hit_rate >= 0.4 else "red"
        console.print(
            f"\n[bold]Overview[/bold]  "
            f"sessions: [cyan]{len(sessions)}[/cyan]  "
            f"projects: [magenta]{len(projects_set)}[/magenta]  "
            f"calls: [cyan]{total['calls']}[/cyan]  "
            f"cache hit: [{hit_color}]{cache_hit_rate*100:.1f}%[/{hit_color}]  "
            f"total cost: [bold green]{fmt_cost(total['cost'])}[/bold green]"
        )

        # Section 2: by model — with cache hit % and % of total cost
        by_model = aggregate(records, lambda r: r.model)
        peak_model = max((a["cost"] for a in by_model.values()), default=1.0)
        t = Table(title="By Model", box=box.HEAVY_HEAD, show_footer=True)
        t.add_column("Model", footer="[bold]TOTAL[/bold]")
        t.add_column("Calls", justify="right", footer=f"[bold]{total['calls']}[/bold]")
        t.add_column("Input", justify="right")
        t.add_column("Output", justify="right")
        t.add_column("Cache R", justify="right")
        t.add_column("Cache W", justify="right")
        t.add_column("Hit %", justify="right",
                     footer=f"[{hit_color}]{cache_hit_rate*100:.0f}%[/{hit_color}]")
        t.add_column("Cost", justify="right",
                     footer=f"[bold green]{fmt_cost(total['cost'])}[/bold green]")
        t.add_column("% cost", justify="right", footer="[bold]100%[/bold]")
        for model, a in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
            inp_like = a["in"] + a["cr"] + a["cw"]
            hp = a["cr"] / inp_like * 100 if inp_like else 0.0
            hc = "green" if hp >= 70 else "yellow" if hp >= 40 else "red"
            cp = a["cost"] / total["cost"] * 100 if total["cost"] else 0.0
            t.add_row(
                model, str(a["calls"]),
                fmt_num(a["in"]), fmt_num(a["out"]),
                fmt_num(a["cr"]), fmt_num(a["cw"]),
                f"[{hc}]{hp:.0f}%[/{hc}]",
                _cost_markup(a["cost"], peak_model),
                f"{cp:.1f}%",
            )
        console.print(t)

        # Section 3: daily (last 30) — with cost bar and color
        by_day = aggregate(
            records,
            lambda r: parse_ts(r.timestamp).date().isoformat() if parse_ts(r.timestamp) else None,
        )
        days = sorted(by_day.keys(), reverse=True)[:30]
        day_costs = [by_day[d]["cost"] for d in days]
        peak_day = max(day_costs) if day_costs else 1.0
        bar_w = 12
        t = Table(title=f"Daily (last {len(days)})", box=box.HEAVY_HEAD, show_footer=True)
        t.add_column("Date", footer="[bold]TOTAL[/bold]")
        t.add_column("Calls", justify="right",
                     footer=f"[bold]{sum(by_day[d]['calls'] for d in days)}[/bold]")
        t.add_column("Input", justify="right")
        t.add_column("Output", justify="right")
        t.add_column("Cache R", justify="right")
        t.add_column("Cache W", justify="right")
        t.add_column("Cost", justify="right",
                     footer=f"[bold green]{fmt_cost(sum(day_costs))}[/bold green]")
        t.add_column("", justify="left", no_wrap=True)
        for d in days:
            a = by_day[d]
            bar = "█" * int(a["cost"] / peak_day * bar_w)
            t.add_row(
                d, str(a["calls"]),
                fmt_num(a["in"]), fmt_num(a["out"]),
                fmt_num(a["cr"]), fmt_num(a["cw"]),
                _cost_markup(a["cost"], peak_day),
                f"[cyan]{bar}[/cyan]",
            )
        console.print(t)

        # Section 4: top projects (omit when already filtered to one project)
        if not project_label:
            by_project = aggregate(records, lambda r: r.project)
            peak_proj = max((a["cost"] for a in by_project.values()), default=1.0)
            t = Table(title="Top 15 Projects", box=box.HEAVY_HEAD)
            t.add_column("Project")
            t.add_column("Calls", justify="right")
            t.add_column("Input", justify="right")
            t.add_column("Output", justify="right")
            t.add_column("Cost", justify="right")
            t.add_column("% cost", justify="right")
            t.add_column("Last active", justify="right")
            for project, a in sorted(by_project.items(), key=lambda kv: -kv[1]["cost"])[:15]:
                last = a["last"].strftime("%Y-%m-%d") if a["last"] else "-"
                cp = a["cost"] / total["cost"] * 100 if total["cost"] else 0.0
                t.add_row(
                    shorten_path(decode_project(project)), str(a["calls"]),
                    fmt_num(a["in"]), fmt_num(a["out"]),
                    _cost_markup(a["cost"], peak_proj),
                    f"{cp:.1f}%",
                    last,
                )
            console.print(t)

        # Section 5: heatmap
        grid = [[0.0] * 24 for _ in range(7)]
        for r in records:
            ts = parse_ts(r.timestamp)
            if ts is None:
                continue
            local = ts.astimezone()
            grid[local.weekday()][local.hour] += r.cost
        peak = max(max(row) for row in grid) or 1.0
        console.print(f"\n[bold]Usage Heatmap (cost, local time)[/bold]")
        hour_tens = "".join(f"{h//10 if h % 5 == 0 else ' '}" for h in range(24))
        hour_ones = "".join(str(h % 10) for h in range(24))
        console.print(f"       {hour_tens}")
        console.print(f"       {hour_ones}")
        dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for dow in range(7):
            cells = "".join(_heat_cell(grid[dow][h] / peak) for h in range(24))
            row_total = sum(grid[dow])
            console.print(f"  [bold]{dow_labels[dow]}[/bold]  {cells}  {fmt_cost(row_total):>8s}")

    # Save
    out = args.output or f"claude-usage-{date.today().isoformat()}.{args.format}"
    if args.format == "html":
        console.save_html(out, inline_styles=True)
    elif args.format == "svg":
        console.save_svg(out, title="Claude Code Usage Report")
    elif args.format == "txt":
        console.save_text(out)
    else:
        print(f"Unknown format: {args.format}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote report -> {out}", file=sys.stderr)
    if args.format == "html":
        print("Open in a browser and use File > Print > Save as PDF for a PDF copy.",
              file=sys.stderr)


def cmd_activity(args) -> None:
    """Per-day engagement: unique sessions active, unique projects touched."""
    records = load_all()

    @dataclass
    class DayStats:
        sessions: set
        projects: set
        project_calls: dict  # project -> call count (for top-project-of-day)
        calls: int
        cost: float

    by_day: dict[str, DayStats] = defaultdict(
        lambda: DayStats(set(), set(), defaultdict(int), 0, 0.0)
    )
    for r in records:
        ts = parse_ts(r.timestamp)
        if ts is None:
            continue
        d = ts.astimezone().date().isoformat()
        a = by_day[d]
        if r.session_id:
            a.sessions.add(r.session_id)
        if r.project:
            a.projects.add(r.project)
            a.project_calls[r.project] += 1
        a.calls += 1
        a.cost += r.cost

    if not by_day:
        print("No dated records found.")
        return

    days = sorted(by_day.keys())
    if args.days:
        days = days[-args.days:]

    # Sparkline helper (reused below)
    bars = " ▁▂▃▄▅▆▇█"
    def spark(values: list[float]) -> str:
        peak = max(values) or 1.0
        return "".join(bars[min(len(bars) - 1, int(v / peak * (len(bars) - 1)))] for v in values)

    sessions_series = [len(by_day[d].sessions) for d in days]
    projects_series = [len(by_day[d].projects) for d in days]
    calls_series    = [by_day[d].calls for d in days]
    cost_series     = [by_day[d].cost for d in days]

    if not RICH:
        print(f"Sessions  {spark(sessions_series)}")
        print(f"Projects  {spark(projects_series)}")
        print(f"Calls     {spark(calls_series)}")
        print(f"Cost      {spark(cost_series)}")
        for d in days:
            a = by_day[d]
            print(f"{d}  sessions={len(a.sessions):2d}  projects={len(a.projects):2d}  "
                  f"calls={a.calls:4d}  cost={fmt_cost(a.cost)}")
        return

    console = Console()
    console.print(f"[bold]Activity — last {len(days)} active days[/bold]")
    console.print(f"  Sessions/day  [cyan]{spark(sessions_series)}[/cyan]  "
                  f"peak [bold]{max(sessions_series)}[/bold]")
    console.print(f"  Projects/day  [magenta]{spark(projects_series)}[/magenta]  "
                  f"peak [bold]{max(projects_series)}[/bold]")
    console.print(f"  Calls/day     [green]{spark(calls_series)}[/green]  "
                  f"peak [bold]{max(calls_series)}[/bold]")
    console.print(f"  Cost/day      [yellow]{spark(cost_series)}[/yellow]  "
                  f"peak [bold]{fmt_cost(max(cost_series))}[/bold]")

    peak_sessions = max(sessions_series) or 1
    peak_projects = max(projects_series) or 1

    t = Table(title="Daily Activity", box=box.SIMPLE_HEAVY)
    t.add_column("Date", no_wrap=True)
    t.add_column("Sess", justify="right", no_wrap=True)
    t.add_column("Proj", justify="right", no_wrap=True)
    t.add_column("Calls", justify="right", no_wrap=True)
    t.add_column("Cost", justify="right", no_wrap=True)
    t.add_column("Top project (calls)", overflow="ellipsis")

    for d in days:
        a = by_day[d]
        ns, np = len(a.sessions), len(a.projects)
        # highlight busy days
        sess_str = f"[bold cyan]{ns}[/bold cyan]" if ns >= peak_sessions * 0.7 else str(ns)
        proj_str = f"[bold magenta]{np}[/bold magenta]" if np >= peak_projects * 0.7 else str(np)
        if a.project_calls:
            top_proj_enc, top_calls = max(a.project_calls.items(), key=lambda kv: kv[1])
            top_proj = shorten_path(decode_project(top_proj_enc))
            top_proj_cell = f"{top_proj} [dim]({top_calls})[/dim]"
        else:
            top_proj_cell = ""
        t.add_row(d, sess_str, proj_str, str(a.calls), fmt_cost(a.cost), top_proj_cell)
    console.print(t)

    # Rolling summary
    total_sessions = len({s for d in days for s in by_day[d].sessions})
    total_projects = len({p for d in days for p in by_day[d].projects})
    avg_proj_per_day = sum(projects_series) / len(days)
    console.print(
        f"\n[bold]Window total:[/bold] {total_sessions} unique sessions across "
        f"{total_projects} unique projects  "
        f"[bold]avg projects/day:[/bold] {avg_proj_per_day:.1f}  "
        f"[bold]cost:[/bold] [green]{fmt_cost(sum(cost_series))}[/green]"
    )


def cmd_budget(args) -> None:
    """Check today's and this month's spend against daily/monthly limits."""
    records = load_all()
    today = date.today()
    month_start = today.replace(day=1)

    today_cost = 0.0
    month_cost = 0.0
    for r in records:
        ts = parse_ts(r.timestamp)
        if ts is None:
            continue
        d = ts.astimezone().date()
        if d == today:
            today_cost += r.cost
        if d >= month_start:
            month_cost += r.cost

    # build rows: (label, spent, limit)
    rows: list[tuple[str, float, float | None]] = []
    if args.daily is not None:
        rows.append(("Today", today_cost, args.daily))
    else:
        rows.append(("Today", today_cost, None))
    if args.monthly is not None:
        rows.append((f"Month ({month_start:%b %Y})", month_cost, args.monthly))
    else:
        rows.append((f"Month ({month_start:%b %Y})", month_cost, None))

    worst_frac = 0.0
    if not RICH:
        for label, spent, limit in rows:
            if limit:
                frac = spent / limit
                worst_frac = max(worst_frac, frac)
                print(f"{label:20s}  {fmt_cost(spent)} / {fmt_cost(limit)}  ({frac*100:5.1f}%)")
            else:
                print(f"{label:20s}  {fmt_cost(spent)}  (no limit set)")
    else:
        console = Console()
        t = Table(title="Cost Budget", box=box.SIMPLE_HEAVY)
        t.add_column("Scope", no_wrap=True); t.add_column("Spent", justify="right", no_wrap=True)
        t.add_column("Limit", justify="right", no_wrap=True); t.add_column("%", justify="right", no_wrap=True)
        t.add_column("Progress", justify="left", no_wrap=True); t.add_column("Status", justify="right", no_wrap=True)
        for label, spent, limit in rows:
            if limit:
                frac = spent / limit
                worst_frac = max(worst_frac, frac)
                bar_width = 16
                filled = min(bar_width, int(frac * bar_width))
                if frac >= 1.0:
                    color = "red"; status = "[bold red]OVER[/bold red]"
                elif frac >= args.warn_at:
                    color = "yellow"; status = f"[bold yellow]WARN >{int(args.warn_at*100)}%[/bold yellow]"
                else:
                    color = "green"; status = "[green]ok[/green]"
                bar = f"[{color}]{'█' * filled}[/{color}]{'░' * (bar_width - filled)}"
                t.add_row(label, fmt_cost(spent), fmt_cost(limit),
                          f"{frac*100:.1f}%", bar, status)
            else:
                t.add_row(label, fmt_cost(spent), "-", "-", "", "[dim]no limit[/dim]")
        console.print(t)
        if worst_frac >= 1.0:
            console.print(f"[bold red]Budget exceeded.[/bold red]")
        elif worst_frac >= args.warn_at:
            console.print(f"[bold yellow]Approaching budget limit "
                          f"({worst_frac*100:.0f}% of worst scope).[/bold yellow]")

    if args.strict:
        if worst_frac >= 1.0:
            sys.exit(1)
        if worst_frac >= args.warn_at:
            sys.exit(2)
    sys.exit(0)


# -------------------------------- CLI ---------------------------------- #


def main() -> None:
    p = argparse.ArgumentParser(
        prog="monitor",
        description="Monitor Claude Code token usage and estimated costs.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary", help="Overall totals and per-model breakdown")

    pd = sub.add_parser("daily", help="Daily usage breakdown")
    pd.add_argument("--days", type=int, default=14, help="How many days to show (default 14)")

    pp = sub.add_parser("projects", help="Top projects by cost")
    pp.add_argument("--top", type=int, default=20)

    ps = sub.add_parser("sessions", help="Top sessions by cost")
    ps.add_argument("--top", type=int, default=20)

    pe = sub.add_parser("export", help="Export raw records")
    pe.add_argument("--format", choices=["csv", "json"], default="csv")
    pe.add_argument("--output", "-o", default="-", help="Output path or '-' for stdout")

    pl = sub.add_parser("live", help="Live auto-refreshing dashboard")
    pl.add_argument("--interval", type=float, default=5.0, help="Refresh seconds (min 1)")

    ph = sub.add_parser("heatmap", help="Day-of-week x hour-of-day usage heatmap (local time)")
    ph.add_argument("--metric", choices=["cost", "calls", "tokens"], default="cost")

    pt = sub.add_parser("trend", help="Daily trend for one project (substring match)")
    pt.add_argument("project", help="Path substring, e.g. 'ZeroCTX' or 'Desktop/Code/idea'")
    pt.add_argument("--days", type=int, default=30, help="Limit to last N active days (0 = all)")

    pa = sub.add_parser("activity", help="Per-day unique sessions & projects active (engagement)")
    pa.add_argument("--days", type=int, default=30, help="Limit to last N active days (0 = all)")

    pw = sub.add_parser("weekly", help="Per-ISO-week usage (Mon-Sun buckets)")
    pw.add_argument("--weeks", type=int, default=12, help="Last N weeks (default 12)")

    pc = sub.add_parser("calendar", help="GitHub-style yearly activity grid")
    pc.add_argument("--year", type=int, help="Year to show (default: current year)")
    pc.add_argument("--metric", choices=["cost", "calls"], default="cost")

    pca = sub.add_parser("cache", help="Cache hit rate and estimated savings per project")
    pca.add_argument("--top", type=int, default=15)

    pr = sub.add_parser("report", help="Export a full dashboard (HTML/SVG/TXT)")
    pr.add_argument("--format", choices=["html", "svg", "txt"], default="html")
    pr.add_argument("--output", "-o", help="Output path (default: claude-usage-<date>.<ext>)")
    pr.add_argument("--width", type=int, default=140, help="Render width in columns")
    pr.add_argument("--project", help="Filter to one project (substring match, e.g. 'my-app')")

    pb = sub.add_parser("budget", help="Check spend vs daily/monthly limits")
    pb.add_argument("--daily", type=float, help="Daily budget in USD, e.g. 10")
    pb.add_argument("--monthly", type=float, help="Monthly budget in USD, e.g. 200")
    pb.add_argument("--warn-at", type=float, default=0.8,
                    help="Warn when spend reaches this fraction of limit (default 0.8)")
    pb.add_argument("--strict", action="store_true",
                    help="Exit 1 if over limit, 2 if over warn threshold (for scripts)")

    args = p.parse_args()
    handlers = {
        "summary":  cmd_summary,
        "daily":    cmd_daily,
        "projects": cmd_projects,
        "sessions": cmd_sessions,
        "export":   cmd_export,
        "live":     cmd_live,
        "heatmap":  cmd_heatmap,
        "trend":    cmd_trend,
        "activity": cmd_activity,
        "weekly":   cmd_weekly,
        "calendar": cmd_calendar,
        "cache":    cmd_cache,
        "report":   cmd_report,
        "budget":   cmd_budget,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
