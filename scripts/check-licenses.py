#!/usr/bin/env python3
"""Dependency licence gate + THIRD-PARTY-NOTICES generator (CONTRIBUTING rule 40, ADR 0031).

    check-licenses.py backend  [--requirements FILE] [--notices OUT.md]
    check-licenses.py frontend [--prod]
    check-licenses.py all          # backend (requirements-dev.txt) + frontend (whole tree)

backend: walks the installed closure of the requirements file (the interpreter running this
script must be the one the file was installed into) and reads each distribution's licence
metadata + bundled LICENSE / NOTICE files. frontend: `pnpm licenses list --json` over the
lockfile. A dependency whose licence is not on the allowlist — or is unknown — fails, unless
scripts/licenses/overrides.toml records a reviewed override with evidence. The backend image
build runs this with --notices, so a violation also fails the build.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "scripts" / "licenses" / "overrides.toml"
FRONTEND = ROOT / "frontend"

ALLOWED = {
    "MIT",
    "MIT-0",
    "X11",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "Apache-2.0",
    "ISC",
    "PSF-2.0",
    "Python-2.0",
    "MPL-2.0",
    "LGPL",
    "LGPL-2.0",
    "LGPL-2.1",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "Unlicense",
    "Zlib",
    "CC0-1.0",
    "CC-BY-4.0",
    "BlueOak-1.0.0",
    "OFL-1.1",
    "HPND",
    "MIT-CMU",
}

FORBIDDEN = re.compile(
    r"(?<![A-Za-z])A?GPL(?!.*Lesser)|Affero|SSPL|Server Side Public|BUSL|Business Source"
    r"|Elastic|Commons.Clause|Artistic|CC-BY-NC|NonCommercial|Proprietary|UNKNOWN",
    re.I,
)

# Trove classifiers and free-text spellings → SPDX ids.
_SPELLINGS = {
    "mit license": "MIT",
    "mit": "MIT",
    "mit-0": "MIT-0",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache": "Apache-2.0",
    "bsd license": "BSD",
    "bsd": "BSD",
    "3-clause bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "isc license (iscl)": "ISC",
    "isc": "ISC",
    "python software foundation license": "PSF-2.0",
    "psf": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "unlicense": "Unlicense",
    "gnu library or lesser general public license (lgpl)": "LGPL",
    "gnu lesser general public license v2 (lgplv2)": "LGPL-2.0",
    "gnu lesser general public license v2.1 (lgplv2.1)": "LGPL-2.1",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "zlib/libpng license": "Zlib",
    "zlib": "Zlib",
    "cc0 1.0 universal (cc0 1.0) public domain dedication": "CC0-1.0",
    "freely distributable": "Freely-Distributable",
}


def canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def normalise(atom: str) -> str:
    text = atom.strip().strip('"').rstrip(".")
    text = re.sub(r"^(license\s*::\s*)?(osi approved\s*::\s*)?", "", text, flags=re.I)
    return _SPELLINGS.get(text.lower(), text)


def _tokens(expression: str) -> list[str]:
    # Classifier lists arrive "; "-joined — several licences the user picks between, so `;` = OR.
    # A classifier spelling is mapped whole first: its own parentheses are not grouping.
    expression = " OR ".join(normalise(piece) for piece in expression.split(";"))
    return [
        t for t in re.split(r"(\(|\)|\s+(?:AND|OR|WITH)\s+)", expression, flags=re.I) if t.strip()
    ]


def allowed(expression: str, allow: set[str] = ALLOWED) -> bool:
    """Evaluate an SPDX-style expression: an OR needs one allowed side, an AND needs both."""
    tokens = _tokens(expression)
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        value = parse_and()
        while pos < len(tokens) and tokens[pos].strip().upper() == "OR":
            pos += 1
            value = parse_and() or value
        return value

    def parse_and() -> bool:
        nonlocal pos
        value = parse_atom()
        while pos < len(tokens) and tokens[pos].strip().upper() == "AND":
            pos += 1
            value = parse_atom() and value
        return value

    def parse_atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            return False
        piece = tokens[pos]
        pos += 1
        if piece == "(":
            value = parse_or()
            pos += 1  # ')'
            return value
        if pos < len(tokens) and tokens[pos].strip().upper() == "WITH":
            pos += 2  # exceptions (e.g. LLVM-exception) do not change the base grant
        return normalise(piece) in allow

    result = parse_or()
    return bool(tokens) and result and pos == len(tokens)


@dataclass
class Package:
    name: str
    version: str
    licence: str
    homepage: str = ""
    author: str = ""
    texts: list[tuple[str, str]] = field(default_factory=list)
    override: str = ""


def load_overrides(path: Path = OVERRIDES) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for section in ("backend", "frontend"):
        for name, entry in data.get(section, {}).items():
            for key in ("license", "evidence", "reason"):
                if not entry.get(key):
                    raise SystemExit(f"{path}: [{section}.{name!r}] needs `{key}`")
            out[f"{section}:{canonicalize_name(name)}"] = entry
    return out


def apply_override(pkg: Package, side: str, overrides: dict[str, dict[str, str]]) -> None:
    entry = overrides.get(f"{side}:{canonicalize_name(pkg.name)}")
    if entry is None or allowed(pkg.licence):
        return  # inert once upstream fixes its metadata, so the notice stays what the wheel says
    if entry.get("version") and entry["version"] not in pkg.version.split(", "):
        return
    pkg.override = f"declared {pkg.licence!r}; {entry['reason']} — {entry['evidence']}"
    pkg.licence = entry["license"]


# ── backend ──────────────────────────────────────────────────────────────────


def read_requirements(path: Path) -> list[Requirement]:
    # packaging (a transitive of pytest and GX) is only needed on the backend side, so the
    # frontend check runs on a bare python3.
    from packaging.requirements import InvalidRequirement, Requirement

    out: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            out.extend(read_requirements(path.parent / line[3:].strip()))
            continue
        if line.startswith("-"):
            continue
        try:
            out.append(Requirement(line))
        except InvalidRequirement as exc:
            raise SystemExit(f"{path}: cannot parse {line!r}: {exc}") from exc
    return out


def _licence_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(normalise(c) for c in classifiers)
    free_text = (meta.get("License") or "").strip()
    if free_text and "\n" not in free_text and len(free_text) < 80:
        return free_text
    return "UNKNOWN"


_TEXT_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.I)


def _texts_of(dist: metadata.Distribution) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for entry in dist.files or []:
        parts = entry.parts
        if len(parts) < 2 or not parts[0].endswith(".dist-info"):
            continue
        if parts[1] == "licenses" or _TEXT_FILE.match(parts[-1]):
            try:
                out.append((parts[-1], entry.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
    return sorted(out)


def _homepage(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    for url in meta.get_all("Project-URL") or []:
        label, _, target = url.partition(",")
        if label.strip().lower() in ("homepage", "source", "repository", "source code"):
            return str(target).strip()
    return str(meta.get("Home-page") or "")


def backend_closure(requirements: list[Requirement]) -> list[Package]:
    from packaging.requirements import Requirement

    pending: list[tuple[str, frozenset[str]]] = []
    stale: list[str] = []
    for req in requirements:
        if req.marker is not None and not req.marker.evaluate():
            continue
        pending.append((req.name, frozenset(req.extras)))
        try:
            installed = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            continue
        if not req.specifier.contains(installed, prereleases=True):
            stale.append(f"{req.name}: pinned {req.specifier}, installed {installed}")
    if stale:
        raise SystemExit(
            "installed versions differ from the pins (install them first): " + "; ".join(stale)
        )
    seen: dict[str, Package] = {}
    extras_seen: set[tuple[str, str]] = set()
    missing: list[str] = []
    while pending:
        name, extras = pending.pop()
        key = canonicalize_name(name)
        try:
            dist = metadata.distribution(key)
        except metadata.PackageNotFoundError:
            missing.append(key)
            continue
        if key not in seen:
            seen[key] = Package(
                name=dist.metadata["Name"],
                version=dist.version,
                licence=_licence_of(dist),
                homepage=_homepage(dist),
                author=dist.metadata.get("Author") or dist.metadata.get("Author-email") or "",
                texts=_texts_of(dist),
            )
        for extra in sorted(extras | {""}):
            if (key, extra) in extras_seen:
                continue
            extras_seen.add((key, extra))
            for spec in dist.requires or []:
                req = Requirement(spec)
                if req.marker is not None and not req.marker.evaluate({"extra": extra}):
                    continue
                pending.append((req.name, frozenset(req.extras)))
    if missing:
        raise SystemExit(
            "not installed in this interpreter (run the check with the environment the "
            f"requirements were installed into): {sorted(set(missing))}"
        )
    return sorted(seen.values(), key=lambda p: p.name.lower())


# ── frontend ─────────────────────────────────────────────────────────────────


def frontend_packages(prod: bool) -> list[Package]:
    cmd = ["pnpm", "licenses", "list", "--json"] + (["--prod"] if prod else [])
    result = subprocess.run(  # noqa: S603 — literal argv
        cmd, cwd=FRONTEND, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{result.stderr}")
    out: list[Package] = []
    for licence, entries in json.loads(result.stdout).items():
        for entry in entries:
            out.append(
                Package(
                    name=entry["name"],
                    version=", ".join(entry.get("versions") or []),
                    licence=entry.get("license") or licence or "UNKNOWN",
                    homepage=entry.get("homepage") or "",
                    author=entry.get("author") or "",
                )
            )
    return sorted(out, key=lambda p: p.name.lower())


# ── report ───────────────────────────────────────────────────────────────────


def violations(packages: list[Package]) -> list[Package]:
    return [p for p in packages if not allowed(p.licence)]


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\", "\\\\").replace("|", "\\|"))


def render_notices(title: str, packages: list[Package]) -> str:
    lines = [
        f"# Third-party notices — {title}",
        "",
        "DataQ is MIT-licensed. This image bundles the following third-party packages under their",
        "own licences; the full licence texts and Apache NOTICE contents shipped with each package",
        "are reproduced below. Generated at build time by `scripts/check-licenses.py`.",
        "",
        f"{len(packages)} packages.",
        "",
        "| Package | Version | Licence | Homepage |",
        "|---|---|---|---|",
    ]
    for p in packages:
        note = " (reviewed override: see below)" if p.override else ""
        cells = (p.name, p.version, f"{p.licence}{note}", p.homepage)
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    lines += ["", "## Licence texts", ""]
    for p in packages:
        lines += [f"### {p.name} {p.version} — {p.licence}", ""]
        if p.author:
            lines += [f"Author: {p.author}", ""]
        if p.override:
            lines += [f"Licence override: {p.override}", ""]
        if not p.texts:
            where = p.homepage or "the project's source distribution"
            lines += [f"No licence text is shipped in the distribution; see {where}.", ""]
        for filename, text in p.texts:
            lines += [f"#### {filename}", "", "```text", text.rstrip("\n"), "```", ""]
    return "\n".join(lines) + "\n"


def check(side: str, packages: list[Package], overrides: dict[str, dict[str, str]]) -> int:
    for p in packages:
        apply_override(p, side, overrides)
    bad = violations(packages)
    print(f"{side}: {len(packages)} packages, {len(bad)} licence violation(s)")
    for p in bad:
        kind = "forbidden" if FORBIDDEN.search(p.licence) else "not on the allowlist"
        print(f"  {p.name} {p.version}: {p.licence!r} — {kind}")
    if bad:
        print(
            "A strong-copyleft or source-available dependency needs an ADR-level exception\n"
            "(rule 40); a package whose metadata misstates a known-permissive licence needs a\n"
            f"reviewed entry in {OVERRIDES.relative_to(ROOT)} with the evidence URL."
        )
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("side", choices=["backend", "frontend", "all"])
    parser.add_argument(
        "--requirements", type=Path, default=ROOT / "backend" / "requirements-dev.txt"
    )
    parser.add_argument("--notices", type=Path, help="backend: write THIRD-PARTY-NOTICES.md here")
    parser.add_argument("--prod", action="store_true", help="frontend: runtime dependencies only")
    args = parser.parse_args(argv[1:])
    overrides = load_overrides()
    rc = 0
    if args.side in ("backend", "all"):
        packages = backend_closure(read_requirements(args.requirements))
        rc |= check("backend", packages, overrides)
        if args.notices and rc == 0:
            args.notices.write_text(render_notices("DataQ backend", packages), encoding="utf-8")
            print(f"wrote {args.notices}")
    if args.side in ("frontend", "all"):
        rc |= check("frontend", frontend_packages(args.prod), overrides)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
