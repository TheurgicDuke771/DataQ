#!/usr/bin/env python3
"""Verify .pre-commit-config.yaml hook `rev:`s == backend/requirements-tooling.txt pins.

The pre-commit hook is the local pre-flight for the CI merge gate (ruff/black/mypy
all run there too). When a hook's `rev:` drifts from the pin CI actually installs,
"pre-commit passing" stops being evidence "CI will pass" — a lint rule present in
CI's version and absent from the hook's produces exactly the failure this guard
exists to prevent, at the slowest possible feedback point (#1473).

Run as a pre-commit local hook (precommit-tooling-sync) to catch drift before push.
Also runnable directly: `python scripts/check-precommit-tooling-sync.py`.

Exit code:
    0 — pins are in sync
    1 — drift detected (with diff printed to stderr)
    2 — could not parse one of the files (config error)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REQS_FILE = REPO_ROOT / "backend" / "requirements-tooling.txt"
PRECOMMIT_FILE = REPO_ROOT / ".pre-commit-config.yaml"

# Maps a requirements-tooling.txt package name to the pre-commit repo URL whose
# `rev:` must match it. Only tools that exist in BOTH files are guarded here —
# bandit/pre-commit itself have no pre-commit-hook counterpart.
_TRACKED_REPOS = {
    "ruff": "astral-sh/ruff-pre-commit",
    "black": "psf/black",
    "mypy": "pre-commit/mirrors-mypy",
}


def _parse_requirements_pins(path: Path) -> dict[str, str]:
    """Return {package: version} for the packages this guard tracks."""
    pins: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9_-]+)(?:\[[^\]]*\])?==([^\s]+)$", line)
        if match and match.group(1).lower() in _TRACKED_REPOS:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _parse_precommit_revs(path: Path) -> dict[str, str]:
    """Return {repo_url_suffix: rev} for every repo block in the config.

    Parsed as YAML rather than matched with a regex against `repo:`/`rev:` line
    adjacency — a comment or blank line between the two (e.g. explaining why a
    version is pinned) is valid YAML but would silently break a positional regex
    match, reporting a false "repo not found" drift error.
    """
    doc = yaml.safe_load(path.read_text())
    revs: dict[str, str] = {}
    for repo in doc.get("repos", []):
        url = repo.get("repo", "")
        rev = repo.get("rev")
        if url.startswith("https://github.com/") and rev is not None:
            revs[url.removeprefix("https://github.com/")] = str(rev)
    return revs


def main() -> int:
    reqs = _parse_requirements_pins(REQS_FILE)
    precommit = _parse_precommit_revs(PRECOMMIT_FILE)

    mismatches: list[str] = []
    for package, repo in _TRACKED_REPOS.items():
        req_version = reqs.get(package)
        if req_version is None:
            print(
                f"ERROR: {package} not found (or unpinned) in "
                f"{REQS_FILE.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
            return 2
        rev = precommit.get(repo)
        if rev is None:
            print(
                f"ERROR: repo {repo} not found in {PRECOMMIT_FILE.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
            return 2
        # pre-commit revs for some tools carry a leading 'v' (e.g. v0.16.3); the
        # requirements pin never does — normalise before comparing.
        if rev.lstrip("v") != req_version:
            mismatches.append(
                f"  {package}: requirements-tooling.txt={req_version} "
                f"vs .pre-commit-config.yaml rev={rev}"
            )

    if not mismatches:
        return 0

    print(
        f"ERROR: drift between {REQS_FILE.relative_to(REPO_ROOT)} and "
        f"{PRECOMMIT_FILE.relative_to(REPO_ROOT)} tool pins.",
        file=sys.stderr,
    )
    for line in mismatches:
        print(line, file=sys.stderr)
    print(
        "\nFix: bump whichever pin is stale so both files name the same version.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
