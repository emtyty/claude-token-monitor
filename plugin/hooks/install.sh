#!/usr/bin/env bash
# Installer for claude-token-monitor plugin.
# Idempotent — safe to re-run (e.g. after `git pull`).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
AGENT_SRC="$PLUGIN_DIR/agents/routine-worker.md"
AGENT_DST="$CLAUDE_HOME/agents/routine-worker.md"
TIER_BLOCK="$PLUGIN_DIR/CLAUDE-tier-routing.md"
CLAUDE_MD="$CLAUDE_HOME/CLAUDE.md"

say() { printf "  %s\n" "$*"; }

# 1. Python deps
echo "Installing token-monitor dependencies..."
if command -v pip >/dev/null 2>&1; then
    pip install "rich>=13.0.0" --quiet && say "✓ rich installed"
elif command -v pip3 >/dev/null 2>&1; then
    pip3 install "rich>=13.0.0" --quiet && say "✓ rich installed (pip3)"
else
    say "⚠ pip/pip3 not found — skipping rich install"
fi

# 2. Install routine-worker subagent (symlinked so `git pull` updates it).
echo "Installing routine-worker subagent..."
mkdir -p "$CLAUDE_HOME/agents"
if [ ! -f "$AGENT_SRC" ]; then
    say "✗ source not found: $AGENT_SRC — abort"
    exit 1
fi
if [ -L "$AGENT_DST" ]; then
    # already a symlink — refresh (target may have moved)
    ln -sfn "$AGENT_SRC" "$AGENT_DST"
    say "✓ symlink refreshed: $AGENT_DST → $AGENT_SRC"
elif [ -e "$AGENT_DST" ]; then
    # regular file exists — back up before replacing
    backup="$AGENT_DST.backup.$(date +%Y%m%d%H%M%S)"
    mv "$AGENT_DST" "$backup"
    ln -s "$AGENT_SRC" "$AGENT_DST"
    say "✓ existing file backed up to $backup and replaced with symlink"
else
    ln -s "$AGENT_SRC" "$AGENT_DST"
    say "✓ symlink created: $AGENT_DST → $AGENT_SRC"
fi

# 3. Install tier-routing as a standalone file, @imported from CLAUDE.md.
#    Non-destructive: never rewrites user's CLAUDE.md — only appends one
#    @import line once (idempotent via grep guard).
TIER_FILE_NAME="claude-token-monitor-tier-routing.md"
TIER_FILE_DST="$CLAUDE_HOME/$TIER_FILE_NAME"
IMPORT_LINE="@$TIER_FILE_NAME"

echo "Installing tier-routing file at $TIER_FILE_DST..."
if [ ! -f "$TIER_BLOCK" ]; then
    say "✗ source not found: $TIER_BLOCK — abort"
    exit 1
fi
cp "$TIER_BLOCK" "$TIER_FILE_DST"
say "✓ tier-routing block written to $TIER_FILE_DST"

echo "Linking tier-routing into $CLAUDE_MD..."
touch "$CLAUDE_MD"
if grep -Fxq "$IMPORT_LINE" "$CLAUDE_MD"; then
    say "✓ @import already present — no change"
else
    # Append with a leading blank line if file is non-empty and doesn't end with one.
    if [ -s "$CLAUDE_MD" ]; then
        # Ensure trailing newline before appending.
        tail -c1 "$CLAUDE_MD" | od -An -c | grep -q '\\n' || printf '\n' >> "$CLAUDE_MD"
        printf '\n%s\n' "$IMPORT_LINE" >> "$CLAUDE_MD"
    else
        printf '%s\n' "$IMPORT_LINE" >> "$CLAUDE_MD"
    fi
    say "✓ appended '$IMPORT_LINE' to $CLAUDE_MD"
fi

echo "Done."
echo
echo "Next steps:"
echo "  • Open a new Claude Code session in any project — global routing is live."
echo "  • To uninstall the tier block:"
echo "      rm $TIER_FILE_DST"
echo "      then remove the '$IMPORT_LINE' line from $CLAUDE_MD"
echo "  • To uninstall the agent: rm $AGENT_DST"
