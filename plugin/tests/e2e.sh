#!/usr/bin/env bash
# End-to-end test: install flow + unit tests + full analyzer pipeline on
# synthetic Claude-Code-shaped session data. All sandbox-based — does not
# touch your real ~/.claude.
#
# Usage: bash plugin/tests/e2e.sh
# Exits non-zero on any failed step.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0
SANDBOX=""

cleanup() { [ -n "$SANDBOX" ] && rm -rf "$SANDBOX"; }
trap cleanup EXIT

step() { printf "\n\033[1m▸ %s\033[0m\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; PASS=$((PASS + 1)); }
bad()  { printf "  ✗ %s\n" "$*"; FAIL=$((FAIL + 1)); }

# ------------------------------------------------------------------
step "Step 1/3: installer regression tests"
# ------------------------------------------------------------------
if bash "$REPO_ROOT/plugin/tests/test_install.sh" > /tmp/e2e-install.log 2>&1; then
    ok "test_install.sh: all asserts pass"
else
    bad "test_install.sh failed — see /tmp/e2e-install.log"
    tail -30 /tmp/e2e-install.log | sed 's/^/    /'
fi

# ------------------------------------------------------------------
step "Step 2/3: check_routing.py unit tests"
# ------------------------------------------------------------------
if python3 "$REPO_ROOT/plugin/tests/test_check_routing.py" -v > /tmp/e2e-unit.log 2>&1; then
    # count tests run from the log (unittest writes this to stderr, which we merged)
    ran="$(grep -Eo 'Ran [0-9]+ tests?' /tmp/e2e-unit.log | tail -1 || echo 'Ran ? tests')"
    ok "$ran"
else
    bad "unit tests failed — see /tmp/e2e-unit.log"
    tail -30 /tmp/e2e-unit.log | sed 's/^/    /'
fi

# ------------------------------------------------------------------
step "Step 3/3: full pipeline — install + simulated sessions + analyzer verdict"
# ------------------------------------------------------------------
SANDBOX="$(mktemp -d "/tmp/e2e-pipeline.XXXXXX")"
FAKE_HOME="$SANDBOX/home"
FAKE_PROJECTS="$FAKE_HOME/.claude/projects"
mkdir -p "$FAKE_HOME/.claude"

# 3a. Run installer against fake home
if CLAUDE_HOME="$FAKE_HOME/.claude" bash "$REPO_ROOT/plugin/hooks/install.sh" \
        > "$SANDBOX/install.log" 2>&1; then
    ok "installer ran cleanly against sandbox home"
else
    # Installer uses `set -e`, so pip failures currently don't propagate,
    # but symlink/CLAUDE.md failures would. Only treat actual script failure
    # as a problem here.
    if grep -q "✓ tier-routing block installed" "$SANDBOX/install.log" \
       && grep -q "✓ symlink" "$SANDBOX/install.log"; then
        ok "installer completed core install (pip unrelated — externally managed)"
    else
        bad "installer did not complete core install — see $SANDBOX/install.log"
        tail -20 "$SANDBOX/install.log" | sed 's/^/    /'
    fi
fi

# 3b. Verify install artifacts
if [ -L "$FAKE_HOME/.claude/agents/routine-worker.md" ]; then
    ok "routine-worker agent symlinked into sandbox home"
else
    bad "routine-worker agent symlink missing"
fi
if grep -q "claude-token-monitor:tier-routing:start" "$FAKE_HOME/.claude/CLAUDE.md" 2>/dev/null; then
    ok "tier-routing block present in sandbox CLAUDE.md"
else
    bad "tier-routing block missing from sandbox CLAUDE.md"
fi

# 3c. Simulate realistic Claude-Code-shaped session data
PROJ="$FAKE_PROJECTS/-Users-meii-Documents-fake-project"
SESSION_ID="sess-e2e-demo"
PARENT_JSONL="$PROJ/$SESSION_ID.jsonl"
SUBAGENT_JSONL="$PROJ/$SESSION_ID/subagents/agent-e2edemo1.jsonl"
mkdir -p "$(dirname "$PARENT_JSONL")" "$(dirname "$SUBAGENT_JSONL")"

# Parent: main Claude delegates a Tier-2 task via Agent(subagent_type='routine-worker')
cat > "$PARENT_JSONL" <<'EOF'
{"type":"user","message":{"content":"fix typo in README line 42"}}
{"type":"assistant","message":{"model":"claude-opus-4-7","content":[{"type":"tool_use","id":"toolu_fake1","name":"Agent","input":{"subagent_type":"routine-worker","description":"fix README typo","prompt":"replace teh -> the"}}]}}
{"type":"user","message":{"content":"done"}}
EOF

# Subagent: routine-worker running on Sonnet
cat > "$SUBAGENT_JSONL" <<'EOF'
{"type":"user","message":{"content":"fix typo"},"agentId":"e2edemo1"}
{"type":"assistant","message":{"model":"claude-sonnet-4-6","content":[{"type":"text","text":"reading file"}]},"agentId":"e2edemo1"}
{"type":"assistant","message":{"model":"claude-sonnet-4-6","content":[{"type":"text","text":"edit applied"}]},"agentId":"e2edemo1"}
{"type":"assistant","message":{"model":"claude-sonnet-4-6","content":[{"type":"text","text":"verified"}]},"agentId":"e2edemo1"}
EOF

ok "wrote synthetic parent JSONL + routine-worker subagent transcript"

# 3d. Run the analyzer
ANALYZER_OUT="$SANDBOX/analyzer.log"
if CLAUDE_PROJECTS="$FAKE_PROJECTS" python3 "$REPO_ROOT/plugin/tests/check_routing.py" \
        --project fake-project --verbose > "$ANALYZER_OUT" 2>&1; then
    ok "analyzer exited 0 (routing confirmed)"
else
    rc=$?
    bad "analyzer exited $rc — see $SANDBOX/analyzer.log"
    cat "$ANALYZER_OUT" | sed 's/^/    /'
fi

# 3e. Verify analyzer output content
if grep -q "Routing confirmed" "$ANALYZER_OUT"; then
    ok "analyzer output contains 'Routing confirmed'"
else
    bad "analyzer output missing 'Routing confirmed' verdict"
fi
if grep -q "1 routine-worker invocation" "$ANALYZER_OUT"; then
    ok "analyzer counted 1 routine-worker invocation"
else
    bad "analyzer did not count the synthetic invocation"
fi
if grep -q "3 Sonnet subagent" "$ANALYZER_OUT"; then
    ok "analyzer counted 3 Sonnet subagent messages"
else
    bad "analyzer did not count synthetic Sonnet messages"
fi

# 3f. Negative-case check: broken routing (subagent runs Opus, not Sonnet) → exit 2
BROKEN_SUB="$PROJ/$SESSION_ID/subagents/agent-broken1.jsonl"
cat > "$BROKEN_SUB" <<'EOF'
{"type":"assistant","message":{"model":"claude-opus-4-7","content":[]},"agentId":"broken1"}
EOF
# Also need to remove the healthy sonnet one so only opus is visible
rm "$SUBAGENT_JSONL"
BROKEN_OUT="$SANDBOX/analyzer-broken.log"
set +e
CLAUDE_PROJECTS="$FAKE_PROJECTS" python3 "$REPO_ROOT/plugin/tests/check_routing.py" \
    --project fake-project > "$BROKEN_OUT" 2>&1
broken_rc=$?
set -e
if [ "$broken_rc" = "2" ]; then
    ok "analyzer returns exit 2 when routine-worker ran Opus (misconfigured)"
else
    bad "analyzer should exit 2 for broken routing, got $broken_rc"
    cat "$BROKEN_OUT" | sed 's/^/    /'
fi

# ------------------------------------------------------------------
printf "\n"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    printf "\033[32mAll %d checks passed.\033[0m\n" "$TOTAL"
    exit 0
else
    printf "\033[31m%d of %d checks failed.\033[0m\n" "$FAIL" "$TOTAL"
    exit 1
fi
