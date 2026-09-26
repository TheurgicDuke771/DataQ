#!/usr/bin/env python3
"""Run ruff/black/mypy from the current env, after checking it matches CI's pins.

The pre-commit hooks for these tools are `language: system` (#2080): they run the
copies installed in the conda env, which installs the same requirements files CI
does. That removes the second, hand-synced list of versions `.pre-commit-config.yaml`
used to carry (hook `rev:`s + mypy's `additional_dependencies`), which Dependabot
never updated — so every bump of one of those packages went red on CI.

What an isolated hook env gave us for free, this restores: "pre-commit passed" is
only evidence "CI will pass" (#1473) if the tool AND, for mypy, the typed deps it
checks against are the versions CI installs. A stale env fails here, loudly, rather
than silently checking with a different version.

Usage (from .pre-commit-config.yaml):
    python scripts/run-pinned-tool.py <tool> [tool args...]

Exit code: the tool's own, or 2 if the env does not match the pins.
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLING = REPO_ROOT / "backend" / "requirements-tooling.txt"
TYPECHECK = REPO_ROOT / "backend" / "requirements-typecheck.txt"

# Each tool is checked against its own pin; mypy's result also depends on the typed
# packages it imports, so those must match too.
_PIN_FILES = {
    "ruff": [TOOLING],
    "black": [TOOLING],
    "mypy": [TOOLING, TYPECHECK],
}

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;#]+)")


def _pins(path: Path) -> dict[str, str]:
    """Return {package: version} for every `name[extras]==version` line."""
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        match = _PIN_RE.match(raw.strip())
        if match:
            out[match.group(1)] = match.group(2)
    return out


def _mismatches(tool: str) -> list[str]:
    pins: dict[str, str] = {}
    for path in _PIN_FILES[tool]:
        file_pins = _pins(path)
        if path == TOOLING:
            file_pins = {tool: file_pins[tool]}
        pins.update(file_pins)
    problems = []
    for package, pinned in sorted(pins.items()):
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "not installed"
        if installed != pinned:
            problems.append(f"  {package}: pinned {pinned}, found {installed}")
    return problems


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in _PIN_FILES:
        print(f"usage: run-pinned-tool.py {{{'|'.join(_PIN_FILES)}}} [args...]", file=sys.stderr)
        return 2
    tool, args = argv[0], argv[1:]
    problems = _mismatches(tool)
    if problems:
        print(
            f"ERROR: the {tool} hook's environment ({sys.executable}) does not match the "
            "versions CI installs:\n" + "\n".join(problems) + "\n\n"
            "Fix: activate the dataq conda env and refresh it —\n"
            "  conda activate dataq && conda env update -n dataq -f environment.yml --prune",
            file=sys.stderr,
        )
        return 2
    # Same interpreter whose packages were just checked — never whatever is first on PATH.
    # S603: `tool` is allowlisted above; `args` are the hook's own flags + the filenames
    # pre-commit passes, as an argv list (no shell).
    return subprocess.call([sys.executable, "-m", tool, *args])  # noqa: S603


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
