#!/usr/bin/env bash
# Scripted demo flow — shown by asciinema in the video.
# Story: monitor finds routine work → plugin auto-routes it to cheaper model.
set -u

PROMPT="\033[1;32m➜\033[0m \033[1;34mclaude-token-monitor\033[0m "
PY="${PY:-python3}"

# Resize the pty that asciinema allocated so rich can render wider (avoid
# column-wrap that leaks partial project names through the sanitizer).
stty cols 140 rows 32 2>/dev/null || true
export COLUMNS=140 LINES=32
COMMENT_C="\033[1;33m"
RESET="\033[0m"

type_cmd() {
  local line="$1"
  printf "$PROMPT"
  for (( i=0; i<${#line}; i++ )); do
    printf "%s" "${line:$i:1}"
    sleep 0.025
  done
  printf "\n"
  sleep 0.35
}

comment() {
  printf "${COMMENT_C}# %s${RESET}\n" "$1"
  sleep 0.8
}

# ------ Act 1: where is the money going? ------
type_cmd "python monitor.py summary"
FORCE_COLOR=1 "$PY" monitor.py summary
sleep 1.5

# ------ Act 2: what's inefficient? ------
type_cmd "python monitor.py suggest --top 5"
FORCE_COLOR=1 "$PY" monitor.py suggest --top 5
sleep 2

comment "↑ lots of routine work is running on Opus — should be on Sonnet"
sleep 1

# ------ Act 3: the fix — auto model routing ------
type_cmd "bash plugin/hooks/install.sh    # adds tier-routing rule"
sleep 0.6
printf "  \033[32m✓\033[0m tier-routing block installed\n"
printf "  \033[32m✓\033[0m routine-worker agent symlinked\n"
sleep 1.2

type_cmd "head -15 plugin/CLAUDE-tier-routing.md"
head -15 plugin/CLAUDE-tier-routing.md
sleep 2.5

comment "Claude Code now auto-delegates routine tasks → Sonnet (≈5× cheaper)"
sleep 2.5
