#!/usr/bin/env bash
# Regression tests for plugin/hooks/install.sh.
# Sandbox-based — touches only /tmp, never your real ~/.claude.
#
# Usage: bash plugin/tests/test_install.sh
# Exits non-zero on any failure.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALLER="$REPO_ROOT/plugin/hooks/install.sh"
AGENT_SRC="$REPO_ROOT/plugin/agents/routine-worker.md"

PASS=0
FAIL=0
SANDBOX=""

setup_sandbox() {
    SANDBOX="$(mktemp -d "/tmp/cim-test.XXXXXX")"
}
teardown_sandbox() {
    [ -n "$SANDBOX" ] && rm -rf "$SANDBOX" || true
    SANDBOX=""
}
trap teardown_sandbox EXIT

run_installer() {
    # Runs installer silently against current sandbox.
    CLAUDE_HOME="$SANDBOX" bash "$INSTALLER" >/dev/null 2>&1 || true
}

assert() {
    # Usage: assert "description" 'shell expression'
    # The expression is eval'd so it can contain &&, ||, !, redirects, etc.
    local desc="$1"; shift
    if eval "$*" >/dev/null 2>&1; then
        printf "  ✓ %s\n" "$desc"; PASS=$((PASS + 1))
    else
        printf "  ✗ %s\n" "$desc"; FAIL=$((FAIL + 1))
    fi
}

is_symlink_to() { [ -L "$1" ] && [ "$(readlink "$1")" = "$2" ]; }
file_contains() { grep -qF "$2" "$1"; }
count_lines() { grep -cF "$1" "$2" 2>/dev/null || echo 0; }

# ---------- Test 1: fresh install on empty sandbox ----------
echo "Test 1: fresh install on empty sandbox"
setup_sandbox
run_installer
assert "CLAUDE.md created"                   "[ -f '$SANDBOX/CLAUDE.md' ]"
assert "agent installed as symlink"          "[ -L '$SANDBOX/agents/routine-worker.md' ]"
assert "symlink points to plugin source"     "is_symlink_to '$SANDBOX/agents/routine-worker.md' '$AGENT_SRC'"
assert "tier block start marker present"     "file_contains '$SANDBOX/CLAUDE.md' 'tier-routing:start'"
assert "tier block end marker present"       "file_contains '$SANDBOX/CLAUDE.md' 'tier-routing:end'"
assert "exactly one start marker"            "[ \$(count_lines 'tier-routing:start' '$SANDBOX/CLAUDE.md') -eq 1 ]"
teardown_sandbox

# ---------- Test 2: idempotency (byte-stable across 3 runs) ----------
echo
echo "Test 2: idempotency (md5 stable across 3 runs)"
setup_sandbox
# seed with realistic user CLAUDE.md
cat > "$SANDBOX/CLAUDE.md" <<'EOF'
# My CLAUDE.md
Pre-existing user rules.
EOF
run_installer
H1="$(md5 -q "$SANDBOX/CLAUDE.md" 2>/dev/null || md5sum "$SANDBOX/CLAUDE.md" | cut -d' ' -f1)"
run_installer
H2="$(md5 -q "$SANDBOX/CLAUDE.md" 2>/dev/null || md5sum "$SANDBOX/CLAUDE.md" | cut -d' ' -f1)"
run_installer
H3="$(md5 -q "$SANDBOX/CLAUDE.md" 2>/dev/null || md5sum "$SANDBOX/CLAUDE.md" | cut -d' ' -f1)"
assert "md5 stable across 3 runs ($H1)"              "[ '$H1' = '$H2' ] && [ '$H2' = '$H3' ]"
assert "user content preserved"                      "file_contains '$SANDBOX/CLAUDE.md' 'Pre-existing user rules.'"
assert "still exactly one start marker after 3 runs" "[ \$(count_lines 'tier-routing:start' '$SANDBOX/CLAUDE.md') -eq 1 ]"
teardown_sandbox

# ---------- Test 3: stale tier block gets replaced, user content preserved ----------
echo
echo "Test 3: stale block replacement"
setup_sandbox
cat > "$SANDBOX/CLAUDE.md" <<'EOF'
# My CLAUDE.md

User rules before.

<!-- claude-token-monitor:tier-routing:start -->
OLD STALE CONTENT TO BE REPLACED
<!-- claude-token-monitor:tier-routing:end -->

User rules after.
EOF
run_installer
assert "stale content removed"         "! file_contains '$SANDBOX/CLAUDE.md' 'OLD STALE CONTENT'"
assert "fresh tier heading present"    "file_contains '$SANDBOX/CLAUDE.md' '## Complexity Tier Routing'"
assert "'user rules before' preserved" "file_contains '$SANDBOX/CLAUDE.md' 'User rules before.'"
assert "'user rules after' preserved"  "file_contains '$SANDBOX/CLAUDE.md' 'User rules after.'"
assert "still exactly one start marker" "[ \$(count_lines 'tier-routing:start' '$SANDBOX/CLAUDE.md') -eq 1 ]"
teardown_sandbox

# ---------- Test 4: pre-existing regular agent file backed up, not silently overwritten ----------
echo
echo "Test 4: regular-file agent gets backed up before symlink replacement"
setup_sandbox
mkdir -p "$SANDBOX/agents"
echo "user's custom version" > "$SANDBOX/agents/routine-worker.md"
run_installer
assert "agent is now symlink" "[ -L '$SANDBOX/agents/routine-worker.md' ]"
assert "a backup file exists" "compgen -G '$SANDBOX/agents/routine-worker.md.backup.*'"
BACKUP="$(ls "$SANDBOX/agents/"routine-worker.md.backup.* 2>/dev/null | head -1)"
assert "backup preserves original content" "[ -f '$BACKUP' ] && grep -q \"user's custom version\" '$BACKUP'"
teardown_sandbox

# ---------- Test 5: re-run doesn't create extra backup when target is already our symlink ----------
echo
echo "Test 5: re-run with existing symlink refreshes in place (no spurious backups)"
setup_sandbox
run_installer  # first run — creates symlink
run_installer  # second run — should refresh, not back up
BACKUP_COUNT="$(ls "$SANDBOX/agents/"routine-worker.md.backup.* 2>/dev/null | wc -l | tr -d ' ')"
assert "zero backup files (symlink refreshed in place)" "[ '$BACKUP_COUNT' -eq 0 ]"
assert "symlink still points to plugin source" "is_symlink_to '$SANDBOX/agents/routine-worker.md' '$AGENT_SRC'"
teardown_sandbox

# ---------- Summary ----------
echo
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo "All $TOTAL tests passed."
    exit 0
else
    echo "$FAIL of $TOTAL tests failed."
    exit 1
fi
