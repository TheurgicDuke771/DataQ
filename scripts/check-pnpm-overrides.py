#!/usr/bin/env python3
"""Every pnpm override in frontend/package.json says why it exists, and can say when it can go.

package.json is JSON, so an override can't carry a comment. frontend/pnpm-overrides.json holds
the reason and the direct dependencies that pull the package. With no arguments this fails when
the two files disagree. With --resolve (the dependency sweep; run `pnpm install` first) it reads
every installed package's declared range for each overridden package, and names the overrides
that no range would take below the floor any more, i.e. the ones that can be removed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
RECORD = FRONTEND / "pnpm-overrides.json"
FLOOR = re.compile(r">=\s*(\d+)\.(\d+)\.(\d+)")
CAP = re.compile(r"<\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _overrides() -> dict[str, str]:
    manifest = json.loads((FRONTEND / "package.json").read_text())
    return dict(manifest.get("pnpm", {}).get("overrides", {}))


def check_record() -> list[str]:
    record = {k: v for k, v in json.loads(RECORD.read_text()).items() if not k.startswith("_")}
    overrides = _overrides()
    problems = [f"{k}: override with no entry in {RECORD.name}" for k in overrides.keys() - record]
    problems += [f"{k}: entry in {RECORD.name} for no override" for k in record.keys() - overrides]
    for key, entry in record.items():
        if not str(entry.get("why", "")).strip() or not entry.get("via"):
            problems.append(f"{key}: needs a non-empty 'why' and 'via'")
    return problems


def _selector(key: str) -> tuple[str, int | None]:
    """`brace-expansion@5` → ("brace-expansion", 5); `@scope/pkg` → ("@scope/pkg", None)."""
    name, sep, major = key.rpartition("@")
    if sep and name and major.isdigit():
        return name, int(major)
    return key, None


def _min_version(spec: str) -> tuple[int, int, int] | None:
    """The lowest version an npm range admits (prerelease tags ignored); None if not a range."""
    lows = []
    for part in spec.split("||"):
        low = (0, 0, 0)
        for comparator in part.split():
            match = re.fullmatch(
                r"(\^|~|>=|>|=)?v?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?.*", comparator
            )
            if not match:
                if comparator.startswith(("<", "*")) or comparator in {"x", "X"}:
                    continue
                return None
            op, *parts = match.groups()
            version = tuple(int(n) if n and n.isdigit() else 0 for n in parts)
            if op == ">":
                version = (version[0], version[1], version[2] + 1)
            low = max(low, version)  # type: ignore[arg-type]
        lows.append(low)
    return min(lows) if lows else None


def still_needed() -> tuple[list[str], list[str]]:
    """Read the installed tree (run `pnpm install` first): for each override, every installed
    package that depends on it and the lowest version its declared range admits. An override is
    still needed while any range admits a version below the floor."""
    edges: dict[str, list[tuple[str, str]]] = {}
    seen: set[Path] = set()
    for manifest in (FRONTEND / "node_modules" / ".pnpm").glob("*/node_modules/**/package.json"):
        real = manifest.resolve()
        if real in seen or "node_modules" in real.relative_to(manifest.parents[1]).parts[:-1]:
            continue
        seen.add(real)
        try:
            data = json.loads(real.read_text())
        except (OSError, ValueError):
            continue
        for section in ("dependencies", "optionalDependencies"):
            for dep, spec in (data.get(section) or {}).items():
                edges.setdefault(dep, []).append(
                    (f"{data.get('name')}@{data.get('version')}", spec)
                )
    needed, gone = [], []
    for key, spec in _overrides().items():
        name, major = _selector(key)
        floor_match = FLOOR.search(spec)
        if not floor_match:
            continue
        floor = tuple(map(int, floor_match.groups()))
        cap_match = CAP.search(spec)
        cap = tuple(int(n or 0) for n in cap_match.groups()) if cap_match else None
        below, capped = [], []
        for dependent, wanted in edges.get(name, []):
            low = _min_version(wanted)
            if low is None or (major is not None and low[0] != major):
                continue
            if low < floor:
                below.append(f"{dependent} wants {wanted}")
            if cap is not None and low >= cap:
                capped.append(f"{dependent} wants {wanted}")
        held = f"; its cap holds back {'; '.join(sorted(set(capped)))}" if capped else ""
        if below:
            needed.append(f"{key}: still needed ({'; '.join(sorted(set(below)))}){held}")
        else:
            gone.append(f"{key}: no installed dependent's range goes below {spec}; remove it{held}")
    return needed, gone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve", action="store_true", help="list overrides no longer needed")
    args = parser.parse_args()
    problems = check_record()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    if args.resolve:
        needed, gone = still_needed()
        print("\n".join(needed + gone))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
