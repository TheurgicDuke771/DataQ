#!/usr/bin/env bash
# Frontend mutation spike (Stryker) — manual / periodic, NOT CI.
#
# Mirrors the backend mutmut spike (CONTRIBUTING.md rule 4a): a hostile pass over
# a pure module to find covered-but-unasserted logic. Stryker + its vitest runner
# are deliberately NOT in frontend/package.json, so they stay off the `pnpm audit`
# merge gate (the backend keeps mutmut off `pip-audit` the same way via a
# standalone requirements-mutation.txt). This script installs them ad-hoc, runs
# Stryker from the project root (Stryker sandboxes the cwd, so it must run there),
# and restores the manifest on exit — even if Stryker fails — so the mutation deps
# never land in the committed package.json / lockfile.
#
# Usage (from anywhere):
#   frontend/mutation/run.sh                         # mutate the configured target
#   frontend/mutation/run.sh --mutate 'src/components/checks/checkForm.ts'
# Pin lives here so spikes are reproducible:
STRYKER_VERSION=9.6.1

set -euo pipefail
cd "$(dirname "$0")/.."   # -> frontend/ (Stryker must run from the project root)

# Refuse to run with a dirty manifest — the EXIT trap restores it via `git
# checkout`, which would clobber any unrelated staged/working changes to it.
if ! git diff --quiet -- package.json pnpm-lock.yaml ||
   ! git diff --cached --quiet -- package.json pnpm-lock.yaml; then
  echo "✗ package.json / pnpm-lock.yaml have uncommitted changes — commit or stash them first" >&2
  exit 1
fi

# Restore the manifest no matter how Stryker exits (keeps the spike deps uncommitted).
trap 'git checkout -- package.json pnpm-lock.yaml 2>/dev/null || true' EXIT

# typescript is already a project dep; Stryker resolves it from node_modules.
# Explicit plugin name in stryker.conf.json — pnpm's strict layout defeats the
# default "@stryker-mutator/*" glob plugin discovery.
pnpm add -D "@stryker-mutator/core@${STRYKER_VERSION}" \
            "@stryker-mutator/vitest-runner@${STRYKER_VERSION}"

pnpm exec stryker run "$@"
