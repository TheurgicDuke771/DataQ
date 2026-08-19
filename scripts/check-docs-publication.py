#!/usr/bin/env python3
"""Every doc must make a deliberate publish/don't-publish choice.

**Omitting a file from mkdocs `nav` does not unpublish it.** MkDocs builds every
`.md` under `docs/` and serves it by URL, indexed by the search plugin — a page
absent from the nav is unlinked, not absent. Only `exclude_docs` keeps a file off
the site.

That is not a hypothetical. `docs/ops-log.md` and
`docs/post-v1-assets-lineage-incidents-notes.md` were both internal, both absent
from `nav`, and both live on the public site — the ops log carrying a personal
email, a live database server name and a credential-expiry calendar. Nobody
decided to publish them; the default did.

So this hook removes the default. Every `docs/**/*.md` must be in exactly one of:

  1. `nav`             — published and linked;
  2. `exclude_docs`    — not published;
  3. `PUBLISHED_UNLINKED` below — published on purpose but not in the nav, which
     today means ADRs: they are reachable from prose and from the ADR index, and
     listing forty of them in the sidebar would bury the guides.

A new file in none of the three fails, with the choice spelled out. The point is
that adding a document should require saying which of those it is.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path("docs")
MKDOCS = Path("mkdocs.yml")

# Published deliberately, but not in the nav. Glob patterns, relative to docs/.
PUBLISHED_UNLINKED = ("adr/*.md",)


def _nav_entries(text: str) -> set[str]:
    """Every `*.md` mentioned in the nav block.

    Deliberately a regex over the nav section rather than a YAML parse: mkdocs.yml
    carries mkdocs-material's `!!python/name:` tags, which the safe loader refuses
    (the repo's own check-yaml hook passes `--unsafe` for the same reason).
    """
    m = re.search(r"^nav:\n(.*?)(?=^\S)", text, re.M | re.S)
    return set(re.findall(r"([A-Za-z0-9_./-]+\.md)", m.group(1))) if m else set()


def _excluded(text: str) -> set[str]:
    m = re.search(r"^exclude_docs:\s*\|\n((?:[ \t]+\S.*\n)+)", text, re.M)
    return set(m.group(1).split()) if m else set()


def main() -> int:
    if not MKDOCS.is_file():
        print("mkdocs.yml not found — run from the repo root")
        return 1
    text = MKDOCS.read_text(encoding="utf-8")
    nav, excluded = _nav_entries(text), _excluded(text)

    unlinked = {
        p.relative_to(DOCS).as_posix() for pat in PUBLISHED_UNLINKED for p in DOCS.glob(pat)
    }

    undeclared = sorted(
        rel
        for p in DOCS.rglob("*.md")
        if (rel := p.relative_to(DOCS).as_posix()) not in nav
        and rel not in excluded
        and rel not in unlinked
    )
    if not undeclared:
        return 0

    print("Docs publication check FAILED — these would publish by default:\n")
    for rel in undeclared:
        print(f"  docs/{rel}")
    print(
        "\nA doc absent from `nav` is UNLINKED, not unpublished — MkDocs still builds it,\n"
        "serves it by URL and indexes it in search. Two internal documents reached the\n"
        "public site exactly this way.\n\n"
        "Decide, in mkdocs.yml:\n"
        "  • public and linked      → add it to `nav`\n"
        "  • internal               → add it to `exclude_docs`\n"
        "  • public but not in nav  → add its pattern to PUBLISHED_UNLINKED in this script,\n"
        "                             which makes that an explicit decision rather than a default"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
