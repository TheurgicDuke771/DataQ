#!/usr/bin/env python3
"""Verify .pre-commit-config.yaml mypy hook deps == backend/requirements-typecheck.txt.

Run as a pre-commit local hook (typecheck-deps-sync) to catch drift before push.
Also runnable directly: `python scripts/check-typecheck-deps.py`.

Exit code:
    0 — lists are in sync
    1 — drift detected (with diff printed to stderr)
    2 — could not parse one of the files (config error)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQS_FILE = REPO_ROOT / "backend" / "requirements-typecheck.txt"
PRECOMMIT_FILE = REPO_ROOT / ".pre-commit-config.yaml"

# Match the mirrors-mypy hook block + its additional_dependencies sub-list.
_MYPY_DEPS_RE = re.compile(
    r"mirrors-mypy.*?additional_dependencies:\s*\n((?:\s*-\s*[^\n]+\n)+)",
    re.DOTALL,
)


def _parse_requirements_txt(path: Path) -> set[str]:
    """Return the pinned packages from a requirements.txt (skip comments + blanks)."""
    out: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def _parse_precommit_mypy_deps(path: Path) -> set[str]:
    """Return additional_dependencies entries from the mirrors-mypy hook."""
    match = _MYPY_DEPS_RE.search(path.read_text())
    if not match:
        print(
            f"ERROR: could not find mirrors-mypy additional_dependencies block in {path}",
            file=sys.stderr,
        )
        sys.exit(2)
    out: set[str] = set()
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if line.startswith("- "):
            out.add(line[2:].strip())
    return out


def main() -> int:
    reqs = _parse_requirements_txt(REQS_FILE)
    precommit = _parse_precommit_mypy_deps(PRECOMMIT_FILE)

    only_reqs = reqs - precommit
    only_precommit = precommit - reqs

    if not only_reqs and not only_precommit:
        return 0

    print(
        f"ERROR: drift between {REQS_FILE.relative_to(REPO_ROOT)} "
        f"and {PRECOMMIT_FILE.relative_to(REPO_ROOT)} mypy hook deps.",
        file=sys.stderr,
    )
    if only_reqs:
        print(
            f"  Only in requirements-typecheck.txt: {sorted(only_reqs)}",
            file=sys.stderr,
        )
    if only_precommit:
        print(
            f"  Only in .pre-commit-config.yaml mypy hook: {sorted(only_precommit)}",
            file=sys.stderr,
        )
    print(
        "\nFix: update whichever file is missing entries so both list the " "same pinned packages.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
