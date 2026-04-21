#!/usr/bin/env python3
"""
check_routing.py — verify that complexity-tier routing is happening.

Scans recent Claude Code session transcripts for:
  1. Parent-side: `Agent` tool invocations with subagent_type == 'routine-worker'
     → proof main Claude delegated a Tier-2 task.
  2. Subagent-side: transcripts under <session>/subagents/agent-*.jsonl
     showing `model` values
     → proof the delegation actually ran on Sonnet (not Opus).

Exit codes:
  0 = routing confirmed (routine-worker invoked AND at least one Sonnet subagent call)
  1 = no routine-worker invocations found in window (nothing to judge)
  2 = invocations found but no Sonnet subagent traffic (routing is broken)

Usage:
  python3 plugin/tests/check_routing.py                  # last 24h, all projects
  python3 plugin/tests/check_routing.py --hours 48
  python3 plugin/tests/check_routing.py --project claude-token-monitor
  python3 plugin/tests/check_routing.py --verbose        # per-session breakdown
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import time


def _resolve_projects_dir() -> pathlib.Path:
    env = os.environ.get("CLAUDE_PROJECTS")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".claude" / "projects"


PROJECTS = _resolve_projects_dir()


def iter_records(path: pathlib.Path):
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def count_routine_worker_uses(parent_file: pathlib.Path) -> int:
    """Count Agent tool invocations with subagent_type=routine-worker."""
    n = 0
    for d in iter_records(parent_file):
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Agent":
                if (b.get("input") or {}).get("subagent_type") == "routine-worker":
                    n += 1
    return n


def subagent_model_counts(subagent_file: pathlib.Path) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for d in iter_records(subagent_file):
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        m = msg.get("model")
        if m:
            c[m] += 1
    return c


def classify_model(model_id: str) -> str:
    s = model_id.lower()
    if "haiku" in s:
        return "haiku"
    if "sonnet" in s:
        return "sonnet"
    if "opus" in s:
        return "opus"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan recent Claude Code transcripts for complexity-tier routing activity."
    )
    ap.add_argument("--hours", type=float, default=24,
                    help="time window in hours (default: 24)")
    ap.add_argument("--project", default=None,
                    help="substring filter on project directory name")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="show per-session breakdown")
    args = ap.parse_args()

    # Resolve at call time (not import time) so CLAUDE_PROJECTS env override
    # works when tests set it after importing the module.
    projects = _resolve_projects_dir()

    if not projects.exists():
        print(f"No Claude Code projects dir at {projects} — no sessions to analyze.")
        return 1

    cutoff = time.time() - args.hours * 3600

    # --- scan parent session files ---
    parent_invocations: dict[str, int] = {}  # session path → count
    for project_dir in sorted(projects.iterdir()):
        if not project_dir.is_dir():
            continue
        if args.project and args.project not in project_dir.name:
            continue
        for session_file in project_dir.glob("*.jsonl"):
            if session_file.stat().st_mtime < cutoff:
                continue
            n = count_routine_worker_uses(session_file)
            if n > 0:
                parent_invocations[str(session_file)] = n

    # --- scan subagent transcripts ---
    subagent_details: list[tuple[pathlib.Path, collections.Counter]] = []
    for project_dir in sorted(projects.iterdir()):
        if not project_dir.is_dir():
            continue
        if args.project and args.project not in project_dir.name:
            continue
        for sub_file in project_dir.rglob("subagents/agent-*.jsonl"):
            if sub_file.stat().st_mtime < cutoff:
                continue
            models = subagent_model_counts(sub_file)
            if models:
                subagent_details.append((sub_file, models))

    # --- aggregate by model bucket ---
    bucket_totals: collections.Counter = collections.Counter()
    for _, models in subagent_details:
        for mid, n in models.items():
            bucket_totals[classify_model(mid)] += n

    total_invocations = sum(parent_invocations.values())
    sonnet_calls = bucket_totals.get("sonnet", 0)
    opus_calls = bucket_totals.get("opus", 0)
    haiku_calls = bucket_totals.get("haiku", 0)

    # --- report ---
    print(f"Window: last {args.hours:g}h"
          + (f"  |  project filter: '{args.project}'" if args.project else ""))
    print(f"Scanned: {len(parent_invocations)} parent session(s) with routine-worker uses · "
          f"{len(subagent_details)} subagent transcript(s) in window")
    print()
    print("Parent-side (main Claude's delegation):")
    print(f"  routine-worker invocations: {total_invocations}")
    print()
    print("Subagent-side (what model the subagent actually ran on):")
    print(f"  Sonnet assistant msgs: {sonnet_calls}")
    print(f"  Opus assistant msgs:   {opus_calls}")
    if haiku_calls:
        print(f"  Haiku assistant msgs:  {haiku_calls}")
    print()

    if args.verbose:
        if parent_invocations:
            print("Sessions with routine-worker invocations:")
            for path, n in sorted(parent_invocations.items()):
                print(f"  {n}× {pathlib.Path(path).relative_to(projects)}")
            print()
        if subagent_details:
            print("Recent subagent transcripts (newest first):")
            for sub_file, models in sorted(
                subagent_details, key=lambda x: x[0].stat().st_mtime, reverse=True
            )[:15]:
                rel = sub_file.relative_to(projects)
                # compact model display
                m_display = ", ".join(f"{k}={v}" for k, v in sorted(models.items()))
                print(f"  {rel}")
                print(f"    models: {m_display}")
            print()

    # --- verdict ---
    if total_invocations == 0:
        print("VERDICT: No routine-worker invocations detected in this window.")
        print("  → Either no Tier-2 tasks were given, or main Claude did not delegate.")
        print("  → Try issuing a concrete Tier-2 task in a fresh session, e.g.:")
        print("    \"đổi chuỗi 'foo' thành 'bar' ở README.md dòng 10\"")
        return 1

    if sonnet_calls == 0:
        print("VERDICT: ⚠ routine-worker was invoked but NO Sonnet subagent traffic found.")
        print("  → The agent likely does not have `model: sonnet` in its frontmatter,")
        print("    or Claude Code fell back to the parent model.")
        print(f"  → Check: head -5 ~/.claude/agents/routine-worker.md")
        return 2

    print(f"VERDICT: ✓ Routing confirmed — {total_invocations} routine-worker "
          f"invocation(s), {sonnet_calls} Sonnet subagent assistant msg(s).")
    if opus_calls:
        ratio = sonnet_calls / (sonnet_calls + opus_calls)
        print(f"         Subagent Sonnet share: {ratio:.0%} "
              f"(Opus subagent msgs also present — mixed subagent types in window)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
