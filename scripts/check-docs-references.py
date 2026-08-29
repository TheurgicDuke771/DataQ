#!/usr/bin/env python3
"""Published docs never cite issue IDs, PR numbers, or link to unpublished docs.

CONTRIBUTING.md rule 41: everything under `docs/site/` (the mkdocs `docs_dir`,
built to the public GitHub Pages site) is read by people with no access to this
repo's issue tracker. A `#1234` or an `.../issues/1234` / `.../pull/1234` link
is internal project-tracking noise to that reader — state the behavior or
decision itself, not the ticket that produced it. Bare GitHub issue/PR
shorthand is never how DataQ's own ADRs cite each other either (that's always
"ADR 0012" or a same-directory `0012-*.md` link, never `#0012`), so this check
needs no ADR-to-ADR exception.

A published page also must never link to an unpublished internal doc
(README/CONTRIBUTING/CLAUDE.md, docs/progress*.md, docs/retro*.md,
docs/ops-log.md) — those aren't reachable from the site, so the link is dead
weight for a reader outside the repo.

(Minimizing an ordinary page's links into docs/site/adr/, and minimizing an
ADR's own links to the PR that shipped it, are judgment calls a script can't
make — that's a review-time check, not this one.)
"""

from __future__ import annotations

import re
from pathlib import Path

SITE = Path("docs/site")

# `#35;` alone (no lookahead: not `#(?!35;)`) is Mermaid's HTML-entity escape for
# a literal `#` in sequence diagrams (35 = ASCII code for '#'), used bare in
# architecture.md's own gotcha note about the convention — not an issue/PR
# reference by itself. `#35;NNN` (the escape immediately followed by digits) IS
# this repo's documented convention for citing an issue *inside* that escape, so
# it's matched separately below rather than excluded outright.
ISSUE_OR_PR_SHORTHAND = re.compile(r"#(?!35;)[0-9]{2,5}\b")
MERMAID_ESCAPED_SHORTHAND = re.compile(r"#35;[0-9]{2,5}\b")
ISSUE_OR_PR_LINK = re.compile(r"github\.com/[^)\s]*?/(?:issues|pull)/[0-9]+")

# A doc name optionally followed by a `#fragment` before the closing paren, so
# `deploy/README.md#pre-deploy-checklist` is still caught. `adr/README.md` is
# excluded: that's the published ADR index (docs_dir covers all of docs/site/),
# not the unpublished root README this rule targets.
UNPUBLISHED_DOC_LINK = re.compile(
    r"\]\([^)]*\b("
    r"(?<!adr/)README\.md"
    r"|CONTRIBUTING\.md"
    r"|CLAUDE\.md"
    r"|docs/progress[^)#]*\.md"
    r"|docs/retro[^)#]*\.md"
    r"|docs/ops-log\.md"
    r")(#[^)]*)?\)"
)


def main() -> int:
    if not SITE.is_dir():
        print(f"{SITE} not found — run from the repo root")
        return 1

    violations: list[str] = []
    for path in sorted(SITE.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, label in (
                (ISSUE_OR_PR_SHORTHAND, "issue/PR shorthand"),
                (MERMAID_ESCAPED_SHORTHAND, "issue/PR shorthand (Mermaid-escaped)"),
                (ISSUE_OR_PR_LINK, "issue/PR link"),
                (UNPUBLISHED_DOC_LINK, "link to an unpublished internal doc"),
            ):
                for m in pattern.finditer(line):
                    violations.append(f"  {path}:{lineno}: {label} — {m.group(0)!r}")

    if not violations:
        return 0

    print("Docs reference check FAILED — published pages must not cite tickets")
    print("or link to unpublished docs (CONTRIBUTING.md rule 41):\n")
    print("\n".join(violations))
    print(
        "\nRewrite the sentence to state the behavior/decision itself instead of\n"
        "pointing at the ticket, or drop the link to the unpublished doc."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
