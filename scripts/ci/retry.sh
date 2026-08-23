# Shared CI retry helper (#539) — source this, don't execute it.

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
