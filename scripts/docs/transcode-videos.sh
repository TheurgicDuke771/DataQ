#!/usr/bin/env bash
# Playwright webm (frontend/test-results/videos-*/video.webm) -> docs/site/assets/videos/<name>.mp4 + .jpg poster.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/docs/site/assets/videos"; mkdir -p "$OUT"
for d in "$ROOT"/frontend/test-results/videos-*-docs-capture; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"; name="${name#videos-}"; name="${name%-docs-capture}"
  src="$(ls "$d"/*.webm | head -1)"
  ffmpeg -y -loglevel error -i "$src" -vf "scale=1440:-2" -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p -movflags +faststart -an "$OUT/$name.mp4"
  ffmpeg -y -loglevel error -ss 2 -i "$OUT/$name.mp4" -frames:v 1 -q:v 4 "$OUT/$name.jpg"
  printf "%s %s KB\n" "$name" "$(du -k "$OUT/$name.mp4" | cut -f1)"
done
