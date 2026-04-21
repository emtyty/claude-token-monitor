#!/usr/bin/env bash
# Capture real monitor.py output as asciinema casts for the demo video.
# Sanitizes $HOME out of the casts so committed fixtures don't leak paths.
#
# Usage: bash plugin/tests/record/capture.sh
# Prereq: brew install asciinema
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

step() { printf "\n\033[1m▸ %s\033[0m\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; }
bad()  { printf "  ✗ %s\n" "$*"; exit 1; }

command -v asciinema >/dev/null || bad "asciinema not installed (brew install asciinema)"

# Use the same 'python3' the user runs normally (matches README: `python monitor.py ...`).
# Override via env if needed, e.g.  PY=python3.12 bash capture.sh
PY="${PY:-python3}"
command -v "$PY" >/dev/null || bad "$PY not on PATH"
"$PY" -c 'import rich' 2>/dev/null || bad "'$PY' missing 'rich' — run: $PY -m pip install rich"
ok "using $PY"

cd "$SCRIPT_DIR"

# ------------------------------------------------------------------
step "recording: monitor.py summary"
# ------------------------------------------------------------------
cd "$REPO_ROOT"  # demo-flow.sh uses relative path to monitor.py
# COLUMNS=140 keeps long paths on one line so sanitize.py can match them.
asciinema rec --overwrite --idle-time-limit 3 \
  --cols 140 --rows 32 \
  --title "claude-token-monitor" \
  --command "COLUMNS=140 LINES=32 PY=$PY bash $SCRIPT_DIR/demo-flow.sh" \
  "$SCRIPT_DIR/summary.cast" \
  && ok "wrote summary.cast" || bad "recording failed"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------
step "regenerating HTML report at width 140"
# ------------------------------------------------------------------
"$PY" "$REPO_ROOT/monitor.py" report --format html --width 140 \
  --output "$SCRIPT_DIR/report.html" >/dev/null \
  && ok "wrote report.html" || bad "report generation failed"

# ------------------------------------------------------------------
step "sanitizing real project paths → generic demo names"
# ------------------------------------------------------------------
"$PY" "$SCRIPT_DIR/sanitize.py"

# ------------------------------------------------------------------
step "sanitizing casts (remove \$HOME paths)"
# ------------------------------------------------------------------
for f in *.cast; do
  sed -i.bak "s|$HOME|~|g" "$f" && rm "$f.bak"
done
ok "sanitized"

printf "\n\033[32mDone.\033[0m Next: bash plugin/tests/record/record.sh\n"
