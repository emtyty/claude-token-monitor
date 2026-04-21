#!/usr/bin/env bash
# Convert Playwright's .webm output → .mp4 for 2 aspect ratios.
# Usage: bash plugin/tests/record/convert.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

command -v ffmpeg >/dev/null || { echo "ffmpeg missing (brew install ffmpeg)"; exit 1; }

find videos -name 'video.webm' | while read -r src; do
  # path looks like: videos/record-demo-desktop-16x9/video.webm
  project="$(basename "$(dirname "$src")")"
  case "$project" in
    *desktop-16x9*) out="demo-16x9.mp4" ;;
    *mobile-9x16*)  out="demo-9x16.mp4" ;;
    *)              out="$project.mp4"  ;;
  esac
  echo "▸ $src → $out"
  ffmpeg -y -nostdin -loglevel error -i "$src" -c:v libx264 -crf 20 -preset slow -pix_fmt yuv420p "$out"
done

echo "Done. Outputs: demo-16x9.mp4, demo-9x16.mp4"
