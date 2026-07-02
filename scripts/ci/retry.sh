# Shared CI retry helper (#539) — source this, don't execute it.
#
# Both Deploy-workflow transients this covers are idempotent to re-run:
#   - `az containerapp update/job update` crashing on a transient ARM error
#     (the containerapp extension's `_polish_bad_errors` masks 409/429-style
#     responses as `KeyError: 'properties'` — Deploys #1/#2, 2026-07-02)
#   - registry blips during image build/push (Docker Hub 502 on the base-image
#     pull — Deploy #3)
#
# Usage:  retry <cmd> [args…]        # 3 attempts, 30s/60s backoff
# The command's exit code is propagated from the final attempt.

retry() {
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    if (( attempt < 3 )); then
      echo "::warning::'$*' failed (attempt ${attempt}/3) — retrying in $(( attempt * 30 ))s (#539)"
      sleep $(( attempt * 30 ))
    fi
  done
  echo "::error::'$*' failed after 3 attempts (#539)"
  return 1
}
