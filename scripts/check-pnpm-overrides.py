#!/usr/bin/env python3
"""Every pnpm override in frontend/package.json says why it exists, and can say when it can go.

package.json is JSON, so an override can't carry a comment. frontend/pnpm-overrides.json holds
the reason and the direct dependencies that pull the package. With no arguments this fails when
the two files disagree. With --resolve (the dependency sweep; run `pnpm install` first) it reads
the declared range of every installed package, and of the frontend itself, for each overridden
package, and reports for each override one of:

- still needed: a range admits a version below the floor (or a range it can't read)
- remove it: no range goes below the floor any more
- its cap holds back: a range wants a version at or above the override's `<N` cap
- unchecked: the override has no `>=` floor to compare against

Exit codes: 0 clean, 1 the record disagrees with package.json, 2 nothing is installed,
3 the sweep found something to act on (a removal, a cap, an unchecked override).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
RECORD = FRONTEND / "pnpm-overrides.json"
FLOOR = re.compile(r">=\s*(\d+)\.(\d+)\.(\d+)")
CAP = re.compile(r"<\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")
SECTIONS = ("dependencies", "optionalDependencies", "peerDependencies", "devDependencies")
# Which of those count for an installed package: its devDependencies are never installed.
INSTALLED_SECTIONS = SECTIONS[:3]
COMPARATOR = re.compile(r"(\^|~|>=|>|<=|<|=)?v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?")

Version = tuple[int, int, int]


def _overrides(frontend: Path = FRONTEND) -> dict[str, str]:
    manifest = json.loads((frontend / "package.json").read_text())
    return dict(manifest.get("pnpm", {}).get("overrides", {}))


def check_record(frontend: Path = FRONTEND) -> list[str]:
    raw = json.loads((frontend / RECORD.name).read_text())
    record = {k: v for k, v in raw.items() if not k.startswith("_")}
    overrides = _overrides(frontend)
    problems = [f"{k}: override with no entry in {RECORD.name}" for k in overrides.keys() - record]
    problems += [f"{k}: entry in {RECORD.name} for no override" for k in record.keys() - overrides]
    for key, entry in sorted(record.items()):
        why = entry.get("why") if isinstance(entry, dict) else None
        via = entry.get("via") if isinstance(entry, dict) else None
        if not (isinstance(why, str) and why.strip()):
            problems.append(f"{key}: needs a non-empty string 'why'")
        if not (isinstance(via, list) and via and all(isinstance(v, str) for v in via)):
            problems.append(f"{key}: needs 'via', a non-empty list of package names")
    return problems


def selector(key: str) -> tuple[str, int | None]:
    """`brace-expansion@5` → ("brace-expansion", 5); `@scope/pkg` → ("@scope/pkg", None)."""
    name, sep, major = key.rpartition("@")
    if sep and name and major.isdigit():
        return name, int(major)
    return key, None


def lows(spec: str) -> list[Version] | None:
    """The lowest version each `||` part of an npm range admits (prerelease tags ignored);
    None if any part is unreadable."""
    spec = spec.strip()
    if spec in {"", "*", "x", "X", "latest"}:
        return [(0, 0, 0)]
    out = []
    for part in spec.split("||"):
        part = part.strip()
        if " - " in part:  # a hyphen range: its lower end
            part = part.split(" - ", 1)[0].strip()
        # `>= 1.2.3` → `>=1.2.3`, so a comparator is one token.
        part = re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", part)
        low: Version = (0, 0, 0)
        for token in part.split():
            match = COMPARATOR.fullmatch(token.split("-", 1)[0])
            if not match:
                return None
            op, *digits = match.groups()
            if op in {"<", "<="}:
                continue
            numbers = [int(d) if d and d.isdigit() else 0 for d in digits]
            version: Version = (numbers[0], numbers[1], numbers[2])
            if op == ">":
                version = (version[0], version[1], version[2] + 1)
            low = max(low, version)
        out.append(low)
    return out


def min_version(spec: str) -> Version | None:
    """The lowest version an npm range admits; None if unreadable."""
    found = lows(spec)
    return min(found) if found else None


def _manifests(frontend: Path) -> Iterator[tuple[str, dict[str, Any], tuple[str, ...]]]:
    """(who, manifest, sections to read) for the frontend and every installed package root."""
    yield "frontend", json.loads((frontend / "package.json").read_text()), SECTIONS
    store = frontend / "node_modules" / ".pnpm"
    for pattern in ("*/node_modules/*/package.json", "*/node_modules/@*/*/package.json"):
        for path in store.glob(pattern):
            if path.parent.is_symlink():  # a dependency link; read where it is stored
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            yield f"{data.get('name')}@{data.get('version')}", data, INSTALLED_SECTIONS


def sweep(frontend: Path = FRONTEND) -> tuple[list[str], bool]:
    """The report lines, and whether any of them needs acting on."""
    if not (frontend / "node_modules" / ".pnpm").is_dir():
        raise FileNotFoundError("frontend/node_modules/.pnpm is missing: run `pnpm install` first")
    edges: dict[str, list[tuple[str, str]]] = {}
    for who, data, sections in _manifests(frontend):
        for section in sections:
            for dep, wanted in (data.get(section) or {}).items():
                if isinstance(wanted, str):
                    edges.setdefault(dep, []).append((who, wanted))
    lines, act = [], False
    for key, spec in sorted(_overrides(frontend).items()):
        name, major = selector(key)
        floor_match = FLOOR.search(spec)
        if not floor_match:
            lines.append(f"{key}: unchecked, {spec!r} has no >= floor to compare against")
            act = True
            continue
        floor = tuple(map(int, floor_match.groups()))
        cap_match = CAP.search(spec)
        cap = tuple(int(n or 0) for n in cap_match.groups()) if cap_match else None
        below, capped = set(), set()
        for who, wanted in edges.get(name, []):
            parts = lows(wanted)
            if parts is None:
                below.add(f"{who} wants {wanted} (unreadable, so counted)")
                continue
            # A `name@N` override applies to major N only: judge the parts of the range in it.
            for low in (p for p in parts if major is None or p[0] == major):
                if low < floor:
                    below.add(f"{who} wants {wanted}")
                if cap is not None and low >= cap:
                    capped.add(f"{who} wants {wanted}")
        held = f"; its cap holds back {'; '.join(sorted(capped))}" if capped else ""
        act = act or bool(capped) or not below
        if below:
            lines.append(f"{key}: still needed ({'; '.join(sorted(below))}){held}")
        else:
            lines.append(f"{key}: no dependent's range goes below {spec}; remove it{held}")
    return lines, act


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve", action="store_true", help="report on every override")
    args = parser.parse_args()
    problems = check_record()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    if not args.resolve:
        return 0
    try:
        lines, act = sweep()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 3 if act else 0


if __name__ == "__main__":
    raise SystemExit(main())
