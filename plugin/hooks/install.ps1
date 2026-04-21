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

# 3. Install tier-routing as a standalone file, @imported from CLAUDE.md.
#    Non-destructive: never rewrites user's CLAUDE.md - only appends one
#    @import line once (idempotent via grep guard).
$TierFileName = "claude-token-monitor-tier-routing.md"
$TierFileDst  = Join-Path $ClaudeHome $TierFileName
$ImportLine   = "@$TierFileName"

Write-Host "Installing tier-routing file at $TierFileDst..."
if (-not (Test-Path $TierBlock)) {
    Write-Host "  ERROR: source not found: $TierBlock" -ForegroundColor Red
    exit 1
}
Copy-Item $TierBlock $TierFileDst -Force
Write-Host "  v tier-routing block written to $TierFileDst"

Write-Host "Linking tier-routing into $ClaudeMd..."
if (-not (Test-Path $ClaudeMd)) { New-Item -ItemType File -Path $ClaudeMd -Force | Out-Null }

$existingLines = Get-Content $ClaudeMd -ErrorAction SilentlyContinue
if ($existingLines | Where-Object { $_ -eq $ImportLine }) {
    Write-Host "  v @import already present - no change"
} else {
    $content = Get-Content $ClaudeMd -Raw -ErrorAction SilentlyContinue
    if ($null -ne $content -and $content.Length -gt 0) {
        # Ensure trailing newline then append with a leading blank line.
        if (-not $content.EndsWith("`n")) { Add-Content -Path $ClaudeMd -Value "" -NoNewline }
        Add-Content -Path $ClaudeMd -Value "`n$ImportLine"
    } else {
        Set-Content -Path $ClaudeMd -Value $ImportLine -NoNewline
    }
    Write-Host "  v appended '$ImportLine' to $ClaudeMd"
}

Write-Host "Done."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - Open a new Claude Code session in any project - global routing is live."
Write-Host "  - To uninstall the tier block:"
Write-Host "      Remove-Item $TierFileDst"
Write-Host "      then remove the '$ImportLine' line from $ClaudeMd"
Write-Host "  - To uninstall the agent: Remove-Item $AgentDst"
