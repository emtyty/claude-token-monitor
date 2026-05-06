#!/usr/bin/env python3
"""
test_monitor.py — fixture-based tests for monitor.py core logic.

Covers:
  - iter_records dedup: multiple JSONL lines sharing the same (sessionId, msg.id)
    must yield exactly one Record with merged tool_use blocks.
  - calc_cost: per-model pricing applied correctly across input / output / cache.
  - filter_records: --since/--until/--last bounds applied correctly.
  - parse_window: --last conflicts with --since; YYYY-MM-DD parsed as local date.
  - _rule_large_context: fires on a session with >=180K single-call context.
  - _rule_opus_routine_session: smoke test that the suggestion engine wires up.

Usage:
    python3 plugin/tests/test_monitor.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

# Make `monitor` importable from repo root regardless of cwd.
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import monitor  # noqa: E402


def _assistant_event(
    *,
    session_id: str,
    msg_id: str,
    timestamp: str,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read: int = 0,
    cache_create: int = 0,
    tools: list[dict] | None = None,
    cwd: str = "/home/u/code/proj",
) -> dict:
    """Build one assistant JSONL event."""
    content: list[dict] = []
    if tools:
        content.extend(tools)
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "message": {
            "id": msg_id,
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
        },
    }


def _write_session(project_dir: Path, session_id: str, events: list[dict]) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    f = project_dir / f"{session_id}.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return f


class IterRecordsDedupTest(unittest.TestCase):
    """One assistant turn → one Record, even when the JSONL has multiple
    lines with the same (sessionId, msg.id) carrying the full usage block."""

    def test_dedup_merges_tool_uses_and_keeps_one_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "c--Users-u-proj"
            sess = "S1"
            mid = "msg_abc"
            ts = "2026-04-15T12:00:00.000Z"
            # Three lines, same (sess, mid). Each carries one tool_use plus
            # the full per-call usage block (Claude Code's actual behavior).
            events = [
                _assistant_event(
                    session_id=sess, msg_id=mid, timestamp=ts,
                    tools=[{"type": "tool_use", "name": "Read",
                            "input": {"file_path": "/a/b.py"}}],
                ),
                _assistant_event(
                    session_id=sess, msg_id=mid, timestamp=ts,
                    tools=[{"type": "tool_use", "name": "Edit",
                            "input": {"file_path": "/a/b.py"}}],
                ),
                _assistant_event(
                    session_id=sess, msg_id=mid, timestamp=ts,
                    tools=[{"type": "tool_use", "name": "Read",
                            "input": {"file_path": "/c/d.py"}}],
                ),
            ]
            _write_session(proj, sess, events)

            records = list(monitor.iter_records(root))
            self.assertEqual(len(records), 1, "duplicate msg.id must collapse")
            r = records[0]
            self.assertEqual(r.session_id, sess)
            self.assertEqual(r.msg_id, mid)
            self.assertEqual(sorted(r.tools), ["Edit", "Read", "Read"])
            self.assertEqual(sorted(r.read_paths), ["/a/b.py", "/c/d.py"])
            # Usage is taken once, not multiplied.
            self.assertEqual(int(r.usage["input_tokens"]), 1000)
            self.assertEqual(int(r.usage["output_tokens"]), 200)

    def test_distinct_msg_ids_are_separate_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "p1"
            sess = "S1"
            ts = "2026-04-15T12:00:00.000Z"
            _write_session(proj, sess, [
                _assistant_event(session_id=sess, msg_id="m1", timestamp=ts),
                _assistant_event(session_id=sess, msg_id="m2", timestamp=ts),
            ])
            records = list(monitor.iter_records(root))
            self.assertEqual(len(records), 2)


class CalcCostTest(unittest.TestCase):
    def test_sonnet_4_pricing(self):
        # 1M input @ $3, 1M output @ $15  →  $18 total
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = monitor.calc_cost(usage, "claude-sonnet-4-6")
        self.assertAlmostEqual(cost, 18.0, places=4)

    def test_opus_4_pricing(self):
        # 1M input @ $15, 1M output @ $75  →  $90
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = monitor.calc_cost(usage, "claude-opus-4-7")
        self.assertAlmostEqual(cost, 90.0, places=4)

    def test_cache_pricing(self):
        # 10M cache_read @ $0.30/1M = $3 (sonnet)
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 10_000_000,
                 "cache_creation_input_tokens": 0}
        cost = monitor.calc_cost(usage, "claude-sonnet-4-6")
        self.assertAlmostEqual(cost, 3.0, places=4)

    def test_unknown_model_falls_back(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cost = monitor.calc_cost(usage, "claude-unknown-vNext")
        # DEFAULT_PRICE is sonnet-equivalent: $3 input → $3
        self.assertAlmostEqual(cost, 3.0, places=4)


class FilterRecordsTest(unittest.TestCase):
    """--since / --until / --last narrow the record set correctly."""

    @staticmethod
    def _ns(**kw):
        ns = argparse.Namespace(since=None, until=None, last=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _records_at(self, dates: list[str]) -> list[monitor.Record]:
        return [
            monitor.Record(
                project="p", session_id=f"s{i}", timestamp=ts,
                model="claude-sonnet-4-6",
                usage={"input_tokens": 0, "output_tokens": 0,
                       "cache_read_input_tokens": 0,
                       "cache_creation_input_tokens": 0},
                cost=0.0, cwd="", msg_id=f"m{i}",
            )
            for i, ts in enumerate(dates)
        ]

    def test_since_excludes_earlier(self):
        recs = self._records_at([
            "2026-04-01T10:00:00+00:00",
            "2026-04-15T10:00:00+00:00",
            "2026-04-30T10:00:00+00:00",
        ])
        kept = monitor.filter_records(recs, self._ns(since="2026-04-15"))
        self.assertEqual(len(kept), 2)

    def test_until_excludes_records_after_bound(self):
        # Use noon UTC and dates far apart so timezone offset (parser uses
        # local zone) cannot accidentally reclassify either record.
        recs = self._records_at([
            "2026-04-10T12:00:00+00:00",
            "2026-04-20T12:00:00+00:00",
        ])
        kept = monitor.filter_records(recs, self._ns(until="2026-04-15"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].timestamp, "2026-04-10T12:00:00+00:00")

    def test_last_conflicts_with_since(self):
        recs = self._records_at(["2026-04-15T10:00:00+00:00"])
        with self.assertRaises(SystemExit):
            monitor.filter_records(recs, self._ns(since="2026-04-01", last="7d"))

    def test_last_window_resolves_relative(self):
        # Make a record at "now-3d" and "now-30d". --last 7d should keep only
        # the 3-day-old one.
        now = datetime.now().astimezone()
        recs = self._records_at([
            (now - timedelta(days=3)).isoformat(),
            (now - timedelta(days=30)).isoformat(),
        ])
        kept = monitor.filter_records(recs, self._ns(last="7d"))
        self.assertEqual(len(kept), 1)

    def test_no_flags_returns_input_unchanged(self):
        recs = self._records_at(["2026-04-15T10:00:00+00:00"])
        kept = monitor.filter_records(recs, self._ns())
        self.assertEqual(kept, recs)


class ParseDurationTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(monitor._parse_duration("30m"), timedelta(minutes=30))
        self.assertEqual(monitor._parse_duration("24h"), timedelta(hours=24))
        self.assertEqual(monitor._parse_duration("7d"),  timedelta(days=7))
        self.assertEqual(monitor._parse_duration("2w"),  timedelta(weeks=2))

    def test_invalid_unit_raises(self):
        with self.assertRaises(ValueError):
            monitor._parse_duration("7y")

    def test_invalid_number_raises(self):
        with self.assertRaises(ValueError):
            monitor._parse_duration("abc")


class LargeContextRuleTest(unittest.TestCase):
    """The new alert: large-context sessions should be flagged with severity
    'high' when any single call >=180K, 'med' when >=150K but <180K."""

    def _make(self, ctx_tokens: int) -> list[monitor.Record]:
        # Put the whole context in cache_read so it counts toward the
        # input-side total used by the rule.
        usage = {"input_tokens": 0, "output_tokens": 200,
                 "cache_read_input_tokens": ctx_tokens,
                 "cache_creation_input_tokens": 0}
        rec = monitor.Record(
            project="p", session_id="bigctx", timestamp="2026-04-15T10:00:00+00:00",
            model="claude-sonnet-4-6",
            usage=usage,
            cost=monitor.calc_cost(usage, "claude-sonnet-4-6"),
            cwd="", msg_id="m1",
        )
        return [rec]

    def test_high_severity_at_180k(self):
        suggestions = monitor.analyze_suggestions(self._make(190_000))
        big = [s for s in suggestions if s.rule == "large-context"]
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0].severity, "high")

    def test_med_severity_at_150k(self):
        suggestions = monitor.analyze_suggestions(self._make(160_000))
        big = [s for s in suggestions if s.rule == "large-context"]
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0].severity, "med")

    def test_no_alert_below_warn_threshold(self):
        suggestions = monitor.analyze_suggestions(self._make(100_000))
        big = [s for s in suggestions if s.rule == "large-context"]
        self.assertEqual(big, [])


class ExpensiveSingleCallRuleTest(unittest.TestCase):
    """expensive-single-call: any single API call > $5 → med, ≥ $10 → high.
    Aggregates per session so N expensive calls in one session = 1 finding."""

    @staticmethod
    def _rec(session_id: str, msg_id: str, cost: float,
             ts: str = "2026-04-15T10:00:00+00:00") -> monitor.Record:
        # Build a Record with the exact target cost — bypass calc_cost so the
        # test isn't pinned to current pricing.
        return monitor.Record(
            project="p", session_id=session_id, timestamp=ts,
            model="claude-opus-4-7",
            usage={"input_tokens": 100_000, "output_tokens": 1_000,
                   "cache_read_input_tokens": 50_000,
                   "cache_creation_input_tokens": 0},
            cost=cost, cwd="", msg_id=msg_id,
        )

    def _findings(self, recs):
        return [s for s in monitor.analyze_suggestions(recs)
                if s.rule == "expensive-single-call"]

    def test_med_when_call_over_5_dollars(self):
        recs = [self._rec("s1", "m1", 6.50)]
        f = self._findings(recs)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "med")

    def test_high_when_any_call_at_or_over_10_dollars(self):
        recs = [self._rec("s1", "m1", 6.0), self._rec("s1", "m2", 12.5)]
        f = self._findings(recs)
        self.assertEqual(len(f), 1, "two expensive calls in one session = one finding")
        self.assertEqual(f[0].severity, "high")

    def test_no_alert_under_threshold(self):
        recs = [self._rec("s1", "m1", 4.99), self._rec("s1", "m2", 0.20)]
        self.assertEqual(self._findings(recs), [])

    def test_separate_sessions_get_separate_findings(self):
        recs = [self._rec("sA", "m1", 7.0), self._rec("sB", "m2", 8.0)]
        f = self._findings(recs)
        self.assertEqual(len(f), 2)
        self.assertEqual({s.scope.split()[1] for s in f}, {"sA", "sB"})

    def test_evidence_reports_peak_cost(self):
        recs = [self._rec("s1", "m1", 6.0), self._rec("s1", "m2", 9.99)]
        f = self._findings(recs)
        self.assertIn("$9.99", f[0].evidence)


class CacheColdSessionRuleTest(unittest.TestCase):
    """cache-cold-session: hit < 30% AND ≥ 5 calls AND cost > $2."""

    @staticmethod
    def _rec(session_id: str, msg_id: str,
             input_tok: int, cache_read: int, cache_write: int = 0,
             output_tok: int = 200,
             ts: str = "2026-04-15T10:00:00+00:00") -> monitor.Record:
        usage = {"input_tokens": input_tok, "output_tokens": output_tok,
                 "cache_read_input_tokens": cache_read,
                 "cache_creation_input_tokens": cache_write}
        return monitor.Record(
            project="p", session_id=session_id, timestamp=ts,
            model="claude-sonnet-4-6",
            usage=usage,
            cost=monitor.calc_cost(usage, "claude-sonnet-4-6"),
            cwd="", msg_id=msg_id,
        )

    def _findings(self, recs):
        return [s for s in monitor.analyze_suggestions(recs)
                if s.rule == "cache-cold-session"]

    def test_fires_on_cold_session(self):
        # 6 calls × 200K raw input each, 1K cache_read → hit rate ≈ 0.5%
        # Cost: 200_000 × 6 × $3/1M = $3.60 → above $2 floor.
        recs = [
            self._rec("cold", f"m{i}", input_tok=200_000, cache_read=1_000)
            for i in range(6)
        ]
        f = self._findings(recs)
        self.assertEqual(len(f), 1)

    def test_no_finding_when_cache_warm(self):
        # 6 calls, 10K input + 100K cache_read → hit rate ≈ 91%
        recs = [
            self._rec("warm", f"m{i}", input_tok=10_000, cache_read=100_000)
            for i in range(6)
        ]
        self.assertEqual(self._findings(recs), [])

    def test_no_finding_under_5_calls(self):
        recs = [
            self._rec("short", f"m{i}", input_tok=200_000, cache_read=1_000)
            for i in range(4)
        ]
        self.assertEqual(self._findings(recs), [])

    def test_no_finding_below_cost_floor(self):
        # 5 small calls → total cost well under $2
        recs = [
            self._rec("cheap", f"m{i}", input_tok=1_000, cache_read=100)
            for i in range(5)
        ]
        self.assertEqual(self._findings(recs), [])


class FullPipelineSmokeTest(unittest.TestCase):
    """End-to-end: write fixtures, redirect projects_root, run iter_records +
    analyze_suggestions, assert at least one rule fires."""

    def test_load_and_analyze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Synthesize an Opus-routine session: 25 calls, all Opus, small
            # outputs (<500 avg). Token sizes chosen so total cost > $5
            # (the rule's lower bound).
            proj = root / "c--Users-u-proj"
            events = [
                _assistant_event(
                    session_id="sess1", msg_id=f"m{i}",
                    timestamp=f"2026-04-15T{i % 24:02d}:00:00.000Z",
                    model="claude-opus-4-6",
                    input_tokens=20_000, output_tokens=300,
                    cache_read=10_000, cache_create=0,
                )
                for i in range(25)
            ]
            _write_session(proj, "sess1", events)

            records = list(monitor.iter_records(root))
            self.assertEqual(len(records), 25)
            suggestions = monitor.analyze_suggestions(records)
            rules = {s.rule for s in suggestions}
            self.assertIn("opus-routine-session", rules,
                          f"expected opus-routine-session suggestion, got {rules}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
