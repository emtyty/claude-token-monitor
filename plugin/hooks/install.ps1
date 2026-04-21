param()
# Installer for claude-token-monitor plugin (Windows).
# Idempotent - safe to re-run. Uses copy (not symlink) because Windows symlinks
# require developer mode or admin. After `git pull`, re-run this script to refresh.
$ErrorActionPreference = "Stop"

$PluginDir   = Split-Path -Parent $PSScriptRoot
$ClaudeHome  = if ($env:CLAUDE_HOME) { $env:CLAUDE_HOME } else { Join-Path $env:USERPROFILE ".claude" }
$AgentSrc    = Join-Path $PluginDir "agents\routine-worker.md"
$AgentDst    = Join-Path $ClaudeHome "agents\routine-worker.md"
$TierBlock   = Join-Path $PluginDir "CLAUDE-tier-routing.md"
$ClaudeMd    = Join-Path $ClaudeHome "CLAUDE.md"
$MarkerStart = "<!-- claude-token-monitor:tier-routing:start -->"
$MarkerEnd   = "<!-- claude-token-monitor:tier-routing:end -->"

# 1. Python deps
Write-Host "Installing token-monitor dependencies..."
try {
    pip install "rich>=13.0.0" --quiet
    Write-Host "  v rich installed"
} catch {
    Write-Host "  ERROR: pip not found. Install Python from https://python.org" -ForegroundColor Red
    exit 1
}

# 2. Install routine-worker subagent (copy; Windows symlinks need dev mode/admin).
Write-Host "Installing routine-worker subagent..."
if (-not (Test-Path $AgentSrc)) {
    Write-Host "  ERROR: source not found: $AgentSrc" -ForegroundColor Red
    exit 1
}
$AgentDstDir = Split-Path -Parent $AgentDst
if (-not (Test-Path $AgentDstDir)) { New-Item -ItemType Directory -Path $AgentDstDir -Force | Out-Null }
if (Test-Path $AgentDst) {
    $backup = "$AgentDst.backup.$(Get-Date -Format 'yyyyMMddHHmmss')"
    Move-Item $AgentDst $backup
    Write-Host "  v existing file backed up to $backup"
}
Copy-Item $AgentSrc $AgentDst
Write-Host "  v agent installed: $AgentDst"
Write-Host "  note: re-run this script after ``git pull`` to pick up agent updates."

# 3. Install/refresh tier-routing block in CLAUDE.md
Write-Host "Installing tier-routing directive in $ClaudeMd..."
if (-not (Test-Path $TierBlock)) {
    Write-Host "  ERROR: source not found: $TierBlock" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $ClaudeMd)) { New-Item -ItemType File -Path $ClaudeMd -Force | Out-Null }

$content = Get-Content $ClaudeMd -Raw
if ($null -eq $content) { $content = "" }

# Strip any existing managed block (between markers, inclusive).
$pattern = [regex]::Escape($MarkerStart) + "[\s\S]*?" + [regex]::Escape($MarkerEnd)
$stripped = [regex]::Replace($content, $pattern, "").TrimEnd()

# Append fresh block.
$block = Get-Content $TierBlock -Raw
$new = if ($stripped.Length -gt 0) { "$stripped`n`n$block" } else { $block }
Set-Content -Path $ClaudeMd -Value $new -NoNewline
Write-Host "  v tier-routing block installed (managed between markers)"

Write-Host "Done."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - Open a new Claude Code session in any project - global routing is live."
Write-Host "  - To uninstall the tier block, remove the block between the"
Write-Host "    claude-token-monitor:tier-routing:* markers in $ClaudeMd."
Write-Host "  - To uninstall the agent: Remove-Item $AgentDst"
