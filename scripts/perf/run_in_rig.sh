#!/usr/bin/env bash
# Run a perf-baseline tag inside the backend image under the DEPLOYED worker's limits.
#
# The dev box has tens of gigabytes, so it measures how much memory a tier WANTS.
# Only a 2 GiB cgroup turns a peak into a SIGKILL, which is the question the
# Iceberg curve exists to answer: which rung dies, not how big the peak is. A
# killed rung comes back as a `killed` row carrying the signal, so the run
# continues up the curve instead of ending at the first death.
#
#   scripts/perf/run_in_rig.sh --build -- --tag iceberg_curve --out /perf-data/curve.json
#   scripts/perf/run_in_rig.sh --memory 4g -- --case iceberg_curve.1m.5checks
#
# Everything after `--` (and every unrecognised argument) is passed to
# `perf_baseline run`. Fixtures live in PERF_DATA_DIR on the host, bind-mounted
# in: the image's own filesystem is read-only to its non-root user, and a fixture
# rebuilt inside each container would be measured as the measurement. Build them
# first, on the host: `python -m backend.scripts.perf_baseline gen-iceberg`.
#
# Live-warehouse tiers: any PERF_SF_* / PERF_UC_* / PERF_ICEBERG_* variable set in
# this shell is forwarded BY NAME (`docker run -e VAR`), so no secret value is
# ever written to a file, an argument list or this script's output.
set -euo pipefail

IMAGE="${PERF_RIG_IMAGE:-dataq-backend:perf}"
MEMORY="${PERF_RIG_MEMORY:-2g}"
CPUS="${PERF_RIG_CPUS:-1}"
DATA_DIR="${PERF_DATA_DIR:-$HOME/.cache/dataq-perf}"
BUILD=0
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --build) BUILD=1; shift ;;
    --) shift; ARGS+=("$@"); break ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [ ${#ARGS[@]} -eq 0 ]; then
  echo "usage: $0 [--image I] [--memory 2g] [--cpus 1] [--data-dir D] [--build]" >&2
  echo "          -- <perf_baseline run args, e.g. --tag iceberg_curve>" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$DATA_DIR"

if [ "$BUILD" = 1 ]; then
  docker build -t "$IMAGE" -f "$REPO_ROOT/backend/Dockerfile" "$REPO_ROOT"
fi

# Forward the warehouse variables by NAME only — never their values.
# `grep -E` + `cut`, not `sed`: BSD sed's basic regex has no `\|` alternation, so
# a sed one-liner matches nothing on macOS and forwards no credential at all.
ENV_FLAGS=()
while IFS= read -r name; do
  [ -n "$name" ] && ENV_FLAGS+=(-e "$name")
done < <(env | grep -E '^PERF_(SF|UC|ICEBERG)_[A-Z0-9_]*=' | cut -d= -f1 || true)

# --user: the image runs as uid 10001, which cannot write the host-owned fixture
# mount. /workspace and the venv are world-readable, so any uid can run the code.
# HOME must be writable — libraries treat it as cache space.
#
# --memory-swap equal to --memory disables swap: with swap the kernel pages
# instead of killing, and a rung that swaps for ten minutes is not a rung that
# survived. `ru_maxrss` is still each child's own, so rows stay comparable.
exec docker run --rm \
  --memory="$MEMORY" --memory-swap="$MEMORY" --cpus="$CPUS" \
  --user "$(id -u):$(id -g)" \
  --env PERF_DATA_DIR=/perf-data \
  --env HOME=/perf-data \
  ${ENV_FLAGS[@]+"${ENV_FLAGS[@]}"} \
  --volume "$DATA_DIR:/perf-data" \
  --workdir /workspace \
  "$IMAGE" \
  python -m backend.scripts.perf_baseline run "${ARGS[@]}"
