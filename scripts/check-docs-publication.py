#!/usr/bin/env python3
"""Every doc must make a deliberate publish/don't-publish choice.

Since the docs/site/ split, **location is the decision**: MkDocs' `docs_dir` is
`docs/site/`, so a file publishes if and only if it lives under `docs/site/`.
Internal planning docs sit directly under `docs/` where the site build never
sees them. (Before the split, everything under `docs/` published by default and
an `exclude_docs` allowlist held the internal set — a default that once put the
ops log, with a personal email and a live database server name, on the public
site because nobody *decided* to publish it.)

Two things are still worth guarding:

  1. `docs_dir` must stay `docs/site` — if it silently reverts to `docs`, every
     internal file publishes again. This hook pins it.
  2. A page added under `docs/site/` should be *findable*: either in `nav`, or
     matching PUBLISHED_UNLINKED below (today: ADRs — reachable from prose and
     the ADR index; forty sidebar entries would bury the guides). A page in
     neither is almost always a nav entry someone forgot, so it fails here —
     as an explicit-decision prompt, not a publication risk.
"""

from __future__ import annotations

import re
from pathlib import Path

SITE = Path("docs/site")
MKDOCS = Path("mkdocs.yml")

# Published deliberately, but not in the nav. Glob patterns, relative to docs/site/.
PUBLISHED_UNLINKED = ("adr/*.md",)


def _nav_entries(text: str) -> set[str]:
    """Every `*.md` mentioned in the nav block.

    Deliberately a regex over the nav section rather than a YAML parse: mkdocs.yml
    carries mkdocs-material's `!!python/name:` tags, which the safe loader refuses
    (the repo's own check-yaml hook passes `--unsafe` for the same reason).
    """
    m = re.search(r"^nav:\n(.*?)(?=^\S)", text, re.M | re.S)
    return set(re.findall(r"([A-Za-z0-9_./-]+\.md)", m.group(1))) if m else set()


def main() -> int:
    if not MKDOCS.is_file():
        print("mkdocs.yml not found — run from the repo root")
        return 1
    text = MKDOCS.read_text(encoding="utf-8")

    m = re.search(r"^docs_dir:\s*(\S+)", text, re.M)
    if not m or m.group(1) != "docs/site":
        print(
            "Docs publication check FAILED — `docs_dir` must be `docs/site`.\n\n"
            f"mkdocs.yml currently has: docs_dir: {m.group(1) if m else '<absent>'}\n\n"
            "The docs/site/ split makes LOCATION the publication decision: internal\n"
            "planning docs live at docs/ and publish only if deliberately moved into\n"
            "docs/site/. Pointing docs_dir back at docs/ would publish every internal\n"
            "file (ops log included) in one line."
        )
        return 1

    if re.search(r"^exclude_docs:", text, re.M):
        print(
            "Docs publication check FAILED — `exclude_docs` is back in mkdocs.yml.\n"
            "With docs_dir at docs/site there is nothing to exclude; an exclude list\n"
            "reintroduces the publish-by-default model this layout replaced. Move the\n"
            "file out of docs/site/ instead."
        )
        return 1

    nav = _nav_entries(text)
    unlinked = {
        p.relative_to(SITE).as_posix() for pat in PUBLISHED_UNLINKED for p in SITE.glob(pat)
    }
    undeclared = sorted(
        rel
        for p in SITE.rglob("*.md")
        if (rel := p.relative_to(SITE).as_posix()) not in nav and rel not in unlinked
    )
    if not undeclared:
        return 0

    print("Docs publication check FAILED — published pages nothing links to:\n")
    for rel in undeclared:
        print(f"  docs/site/{rel}")
    print(
        "\nEverything under docs/site/ IS published (docs_dir roots the site there),\n"
        "so a page missing from `nav` is live but unreachable from the sidebar.\n\n"
        "Decide:\n"
        "  • public and linked      → add it to `nav` in mkdocs.yml\n"
        "  • public but not in nav  → add its pattern to PUBLISHED_UNLINKED in this script\n"
        "  • internal               → move it out of docs/site/ (to docs/)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
