#!/usr/bin/env bash
# Re-encode docs screenshots over the docs-asset limit (scripts/check-docs-assets.py, 350 KB) as
# 256-colour palette PNGs. A busy 2x capture (a drawer over a list) can pass the limit as a
# truecolour PNG; UI screenshots lose nothing visible at 256 colours. Run after a capture.
# Needs ffmpeg, which the clip lane (transcode-videos.sh) already does.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LIMIT=$((350 * 1024))
for png in "$ROOT"/docs/site/assets/screenshots/*.png; do
  size=$(wc -c <"$png")
  [ "$size" -gt "$LIMIT" ] || continue
  tmp="${png%.png}.palette.png"
  ffmpeg -y -loglevel error -i "$png" \
    -vf "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=none" \
    "$tmp"
  mv "$tmp" "$png"
  printf "%s %s KB -> %s KB\n" "$(basename "$png")" "$((size / 1024))" "$(($(wc -c <"$png") / 1024))"
done
