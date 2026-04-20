param()
$ErrorActionPreference = "Stop"

Write-Host "Installing token-monitor dependencies..."
try {
    pip install "rich>=13.0.0" --quiet
    Write-Host "  v rich installed"
} catch {
    Write-Host "  ERROR: pip not found. Install Python from https://python.org" -ForegroundColor Red
    exit 1
}
Write-Host "Done."
