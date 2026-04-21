"""
Unit tests for plugin/tests/check_routing.py.

Runs under stdlib unittest (no external deps):
    python3 -m unittest discover -s plugin/tests

Also picked up by pytest auto-discovery if it's installed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

# Make sibling module importable when running tests by filename.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_routing as cr  # noqa: E402


# ---------- helpers ----------

def write_jsonl(path: pathlib.Path, records: list[dict | str]) -> None:
    """Write a list of records/raw-lines to a JSONL file.
    dict → json.dumps; str → written verbatim (for testing malformed input).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            if isinstance(r, str):
                f.write(r + "\n")
            else:
                f.write(json.dumps(r) + "\n")


def assistant_with_agent_tool(subagent_type: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {"subagent_type": subagent_type},
                }
            ]
        },
    }


def assistant_with_model(model: str) -> dict:
    return {"type": "assistant", "message": {"model": model}}


# ---------- classify_model ----------

class TestClassifyModel(unittest.TestCase):
    def test_haiku(self):
        self.assertEqual(cr.classify_model("claude-haiku-4-5-20251001"), "haiku")

    def test_sonnet(self):
        self.assertEqual(cr.classify_model("claude-sonnet-4-6"), "sonnet")

    def test_opus(self):
        self.assertEqual(cr.classify_model("claude-opus-4-7"), "opus")

    def test_case_insensitive(self):
        self.assertEqual(cr.classify_model("CLAUDE-SONNET-4-6"), "sonnet")

    def test_unknown_returns_other(self):
        self.assertEqual(cr.classify_model("gpt-4-turbo"), "other")

    def test_empty_string_is_other(self):
        self.assertEqual(cr.classify_model(""), "other")


# ---------- iter_records ----------

class TestIterRecords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmpdir.name) / "t.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_parses_valid_lines(self):
        write_jsonl(self.path, [{"a": 1}, {"b": 2}])
        self.assertEqual(list(cr.iter_records(self.path)), [{"a": 1}, {"b": 2}])

    def test_skips_blank_lines(self):
        with self.path.open("w") as f:
            f.write('{"a":1}\n\n\n{"b":2}\n   \n')
        self.assertEqual(list(cr.iter_records(self.path)), [{"a": 1}, {"b": 2}])

    def test_skips_malformed_json(self):
        with self.path.open("w") as f:
            f.write('{"a":1}\n{bad json}\n{"b":2}\n')
        self.assertEqual(list(cr.iter_records(self.path)), [{"a": 1}, {"b": 2}])

    def test_missing_file_returns_empty(self):
        missing = pathlib.Path(self.tmpdir.name) / "does-not-exist.jsonl"
        self.assertEqual(list(cr.iter_records(missing)), [])

    def test_empty_file_returns_empty(self):
        self.path.write_text("")
        self.assertEqual(list(cr.iter_records(self.path)), [])


# ---------- count_routine_worker_uses ----------

class TestCountRoutineWorkerUses(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmpdir.name) / "session.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_counts_matching_invocations(self):
        write_jsonl(self.path, [
            assistant_with_agent_tool("routine-worker"),
            assistant_with_agent_tool("routine-worker"),
        ])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 2)

    def test_ignores_other_subagent_types(self):
        write_jsonl(self.path, [
            assistant_with_agent_tool("Explore"),
            assistant_with_agent_tool("Plan"),
            assistant_with_agent_tool("routine-worker"),
        ])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 1)

    def test_ignores_non_assistant_records(self):
        rec = assistant_with_agent_tool("routine-worker")
        rec["type"] = "user"  # would have counted if we didn't filter by type
        write_jsonl(self.path, [rec])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 0)

    def test_ignores_non_agent_tool_uses(self):
        write_jsonl(self.path, [{
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read",
                     "input": {"subagent_type": "routine-worker"}}
                ]
            }
        }])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 0)

    def test_handles_missing_content(self):
        write_jsonl(self.path, [
            {"type": "assistant", "message": {}},
            {"type": "assistant"},
        ])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 0)

    def test_handles_content_not_list(self):
        # Defensive: content as a string (unusual but seen in some SDK outputs)
        write_jsonl(self.path, [{
            "type": "assistant",
            "message": {"content": "some string content"},
        }])
        self.assertEqual(cr.count_routine_worker_uses(self.path), 0)

    def test_missing_file_zero(self):
        self.assertEqual(cr.count_routine_worker_uses(
            pathlib.Path("/tmp/does-not-exist.jsonl")), 0)


# ---------- subagent_model_counts ----------

class TestSubagentModelCounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmpdir.name) / "sub.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_counts_per_model(self):
        write_jsonl(self.path, [
            assistant_with_model("claude-sonnet-4-6"),
            assistant_with_model("claude-sonnet-4-6"),
            assistant_with_model("claude-opus-4-7"),
        ])
        counts = cr.subagent_model_counts(self.path)
        self.assertEqual(counts["claude-sonnet-4-6"], 2)
        self.assertEqual(counts["claude-opus-4-7"], 1)

    def test_ignores_non_assistant(self):
        write_jsonl(self.path, [
            {"type": "user", "message": {"model": "claude-sonnet-4-6"}},
            assistant_with_model("claude-sonnet-4-6"),
        ])
        counts = cr.subagent_model_counts(self.path)
        self.assertEqual(counts["claude-sonnet-4-6"], 1)

    def test_handles_missing_model(self):
        write_jsonl(self.path, [
            {"type": "assistant", "message": {}},
            assistant_with_model("claude-sonnet-4-6"),
        ])
        counts = cr.subagent_model_counts(self.path)
        self.assertEqual(counts["claude-sonnet-4-6"], 1)
        self.assertEqual(len(counts), 1)

    def test_empty_file_returns_empty_counter(self):
        self.path.write_text("")
        self.assertEqual(dict(cr.subagent_model_counts(self.path)), {})


# ---------- main() verdict exit codes ----------

class TestMainVerdict(unittest.TestCase):
    """Integration tests for the verdict logic. Uses CLAUDE_PROJECTS env to
    point check_routing at a synthetic sandbox and asserts exit code + output."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.sandbox = pathlib.Path(self.tmpdir.name)
        self._saved_env = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = str(self.sandbox)
        self._saved_argv = sys.argv[:]

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("CLAUDE_PROJECTS", None)
        else:
            os.environ["CLAUDE_PROJECTS"] = self._saved_env
        sys.argv[:] = self._saved_argv
        self.tmpdir.cleanup()

    def _run(self, *args: str) -> tuple[int, str]:
        sys.argv = ["check_routing.py", *args]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cr.main()
        return code, buf.getvalue()

    def _make_project_with(
        self,
        project_name: str,
        session_id: str,
        parent_records: list[dict],
        subagent_records_by_id: dict[str, list[dict]] | None = None,
    ) -> None:
        project_dir = self.sandbox / project_name
        parent_file = project_dir / f"{session_id}.jsonl"
        write_jsonl(parent_file, parent_records)
        for agent_id, records in (subagent_records_by_id or {}).items():
            sub_file = project_dir / session_id / "subagents" / f"agent-{agent_id}.jsonl"
            write_jsonl(sub_file, records)

    def test_exit_1_when_projects_dir_missing(self):
        # Point env at a path that doesn't exist
        os.environ["CLAUDE_PROJECTS"] = str(self.sandbox / "definitely-not-here")
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("No Claude Code projects dir", out)

    def test_exit_1_when_no_invocations(self):
        # Empty projects dir (just created) → no data to judge
        code, out = self._run()
        self.assertEqual(code, 1)
        self.assertIn("No routine-worker invocations detected", out)

    def test_exit_2_when_invoked_but_no_sonnet(self):
        # Parent invoked routine-worker, but subagent ran on Opus (misconfigured)
        self._make_project_with(
            project_name="proj-a",
            session_id="sess-1",
            parent_records=[assistant_with_agent_tool("routine-worker")],
            subagent_records_by_id={
                "abc123": [assistant_with_model("claude-opus-4-7")],
            },
        )
        code, out = self._run()
        self.assertEqual(code, 2)
        self.assertIn("NO Sonnet subagent traffic", out)

    def test_exit_0_when_routing_confirmed(self):
        self._make_project_with(
            project_name="proj-b",
            session_id="sess-2",
            parent_records=[
                assistant_with_agent_tool("routine-worker"),
                assistant_with_agent_tool("routine-worker"),
            ],
            subagent_records_by_id={
                "aaa": [
                    assistant_with_model("claude-sonnet-4-6"),
                    assistant_with_model("claude-sonnet-4-6"),
                ],
            },
        )
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("Routing confirmed", out)
        self.assertIn("2 routine-worker invocation(s)", out)
        self.assertIn("2 Sonnet subagent", out)

    def test_project_filter_scopes_results(self):
        # Two projects: only one has the invocation. Filter should pick just it.
        self._make_project_with(
            project_name="match-project",
            session_id="s1",
            parent_records=[assistant_with_agent_tool("routine-worker")],
            subagent_records_by_id={"a1": [assistant_with_model("claude-sonnet-4-6")]},
        )
        self._make_project_with(
            project_name="other-project",
            session_id="s2",
            parent_records=[assistant_with_agent_tool("routine-worker")],
            subagent_records_by_id={"a2": [assistant_with_model("claude-sonnet-4-6")]},
        )
        code, out = self._run("--project", "match-project")
        self.assertEqual(code, 0)
        self.assertIn("1 routine-worker invocation(s)", out)

    def test_hours_filter_excludes_old_files(self):
        # Create invocation, then set its mtime far in the past.
        self._make_project_with(
            project_name="old-project",
            session_id="old-sess",
            parent_records=[assistant_with_agent_tool("routine-worker")],
            subagent_records_by_id={"old": [assistant_with_model("claude-sonnet-4-6")]},
        )
        # backdate every file under sandbox to 100h ago
        import time as _time
        cutoff_time = _time.time() - 100 * 3600
        for p in self.sandbox.rglob("*"):
            if p.is_file():
                os.utime(p, (cutoff_time, cutoff_time))
        code, out = self._run("--hours", "24")
        self.assertEqual(code, 1)  # nothing in last 24h

    def test_verbose_shows_per_session_breakdown(self):
        self._make_project_with(
            project_name="proj-v",
            session_id="sess-v",
            parent_records=[assistant_with_agent_tool("routine-worker")],
            subagent_records_by_id={"vvv": [assistant_with_model("claude-sonnet-4-6")]},
        )
        code, out = self._run("--verbose")
        self.assertEqual(code, 0)
        self.assertIn("Sessions with routine-worker invocations", out)
        self.assertIn("proj-v", out)


if __name__ == "__main__":
    unittest.main()
