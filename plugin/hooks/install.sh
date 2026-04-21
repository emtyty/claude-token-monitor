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
MARKER_START="<!-- claude-token-monitor:tier-routing:start -->"
MARKER_END="<!-- claude-token-monitor:tier-routing:end -->"

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

# 3. Install/refresh tier-routing block in ~/.claude/CLAUDE.md.
echo "Installing tier-routing directive in $CLAUDE_MD..."
if [ ! -f "$TIER_BLOCK" ]; then
    say "✗ source not found: $TIER_BLOCK — abort"
    exit 1
fi
touch "$CLAUDE_MD"

# Strip any existing managed block (between markers, inclusive).
tmp="$(mktemp)"
awk -v start="$MARKER_START" -v end="$MARKER_END" '
    index($0, start)   { skip = 1 }
    !skip              { print }
    index($0, end)     { skip = 0 }
' "$CLAUDE_MD" > "$tmp"

# Trim trailing blank lines / whitespace from the stripped content
# so re-runs produce a byte-stable file (no creeping blank lines).
stripped="$(cat "$tmp")"
while [ "${stripped%$'\n'}" != "$stripped" ] || [ "${stripped%[[:space:]]}" != "$stripped" ]; do
    stripped="${stripped%$'\n'}"
    stripped="${stripped%[[:space:]]}"
done

# Write: pre-existing content (if any) + one blank line + fresh block.
if [ -n "$stripped" ]; then
    {
        printf '%s\n\n' "$stripped"
        cat "$TIER_BLOCK"
    } > "$CLAUDE_MD"
else
    cat "$TIER_BLOCK" > "$CLAUDE_MD"
fi
rm -f "$tmp"
say "✓ tier-routing block installed (managed between markers)"

echo "Done."
echo
echo "Next steps:"
echo "  • Open a new Claude Code session in any project — global routing is live."
echo "  • To uninstall the tier block, remove the block between the"
echo "    <!-- claude-token-monitor:tier-routing:* --> markers in $CLAUDE_MD."
echo "  • To uninstall the agent: rm $AGENT_DST"
