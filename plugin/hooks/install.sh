#!/usr/bin/env bash
set -e

echo "Installing token-monitor dependencies..."
pip install rich>=13.0.0 --quiet && echo "  ✓ rich installed" || {
    pip3 install rich>=13.0.0 --quiet && echo "  ✓ rich installed (pip3)"
}
echo "Done."
