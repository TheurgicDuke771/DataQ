#!/usr/bin/env python3
"""Block ADR/issue/PR references from user-visible frontend strings.

An end user has no use for "(ADR 0038)" or "(#1186)" next to a tooltip — they can't
look either up, and the number is meaningless without repo access. That context
belongs in the PR description and the commit, not in a string a Tag/Tooltip/Alert/
form-field renders on screen.

Comments are a different thing entirely and are NOT in scope here — a `//` line, a
`/** JSDoc */` block, and a `{/* JSX */}` block explaining WHY some UI behaves a
certain way are exactly what CONTRIBUTING wants, and betterleaks-style blanket
banning of "#123" would make every honest code comment illegal. So this script
tracks comment state per line (single-line `//`, block `/* ... */`, which is also
how a JSX comment is written) and only flags a match sitting in actual code — a
string literal, template literal, or JSX text — which is what a browser can render.
The comment tracker is purely state-machine-driven (block-open/close spans a line
boundary correctly); it deliberately does NOT also special-case lines starting with
`*`, because that would silently skip real code that happens to start with an
asterisk after leading whitespace.

## The enclosing-parens requirement

Every real reference this script was written to catch — every ADR/issue/PR mention
found across this entire codebase, comments included — is written wrapped in `(...)`
(`"a coverage gap (ADR 0038)"`, `"(#754/#826)"`). A bare `#NNN` with no parens is far
more likely to be ordinary product copy — a batch, run, row or order number DataQ's
own domain produces plenty of ("Batch #4521 processed") — than a dropped ticket
reference, and an unquoted CSS hex color inside a template-literal style block
(`color: #333;`) is never parenthesized either. So a match only counts when it sits
inside an enclosing, unclosed `(` ... `)` pair on the same line; this is a stricter,
evidence-based version of the plain digit-boundary check the earlier draft used
(which false-flagged CSS hex colors and non-ADR numeric UI copy) and needs no
separate hex-color special case.

`console.*` calls and `data-testid` attribute values are also out of scope: neither
reaches the browser's rendered output (a devtools warning and a test hook,
respectively, not something a user reads), so a ticket reference there — however
questionable a practice — isn't the defect this hook exists to catch.

This is a heuristic, not a parser: a `//` inside a string literal (e.g. a URL) is
treated as a comment starting early, which trades a rare false negative for never
false-flagging a legitimate comment. Good enough for a hook whose job is to catch
the common case before it ships, not to be a TypeScript AST.

Deliberate exceptions take an inline `frontend-ui-ok:` pragma **with a reason**, on
the line or the one above it.

Usage: `check-frontend-no-ticket-refs.py [files...]` — no args means every tracked
file under frontend/src. This same invocation (no args) is also what re-runs the
check as a CI backstop, independent of whether pre-commit ran locally.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PRAGMA = "frontend-ui-ok:"

SCAN_PREFIX = "frontend/src/"
SCAN_SUFFIXES = (".ts", ".tsx")
# Tests render nothing to a real user.
SKIP_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

# Lines whose string content never reaches a browser's rendered output.
NON_RENDERED_MARKERS = (
    "console.log(",
    "console.warn(",
    "console.error(",
    "console.debug(",
    "console.info(",
    "data-testid=",
)

ADR_PATTERN = re.compile(r"\bADR[- ]?\d{2,4}\b", re.IGNORECASE)
# Issue/PR shorthand ("#1186"). A trailing hex-digit or another digit means it's
# not a bare decimal ticket number — bail before the boundary.
TICKET_PATTERN = re.compile(r"#\d{3,5}(?![0-9a-fA-F])")


def _tracked_files() -> list[str]:
    # Literal argv (ruff S603 wants every element provably static, not just constant).
    out = subprocess.run(
        ["/usr/bin/env", "git", "ls-files", "frontend/src/"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def _scannable(path: str) -> bool:
    if not path.startswith(SCAN_PREFIX):
        return False
    if not path.endswith(SCAN_SUFFIXES):
        return False
    if path.endswith(SKIP_SUFFIXES):
        return False
    return True


def _enclosed_in_parens(text: str, start: int, end: int) -> bool:
    """True when `text[start:end]` sits inside an unclosed `(` ... `)` pair —
    see the module docstring for why this is the scope-narrowing rule."""
    before = text[:start]
    last_open = before.rfind("(")
    if last_open == -1 or ")" in before[last_open:]:
        return False
    after = text[end:]
    first_close = after.find(")")
    if first_close == -1 or "(" in after[:first_close]:
        return False
    return True


def _code_spans(line: str) -> list[str]:
    """Return the substrings of `line` that are NOT inside a comment, given the
    line is not a continuation of an already-open block comment (caller tracks
    that). A `//` or `/*` inside a string literal is not distinguished from a
    real comment start — see module docstring."""
    spans = []
    i = 0
    n = len(line)
    while i < n:
        slash_slash = line.find("//", i)
        slash_star = line.find("/*", i)
        if slash_slash == -1 and slash_star == -1:
            spans.append(line[i:])
            break
        if slash_slash != -1 and (slash_star == -1 or slash_slash < slash_star):
            spans.append(line[i:slash_slash])
            break  # rest of the line is a line comment
        # block comment opens first
        spans.append(line[i:slash_star])
        close = line.find("*/", slash_star + 2)
        if close == -1:
            return spans  # stays open past this line; caller handles continuation
        i = close + 2
    return spans


def scan(paths: list[str]) -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for path in paths:
        if not _scannable(path):
            continue
        p = Path(path)
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        in_block_comment = False
        for i, raw_line in enumerate(lines, 1):
            if PRAGMA in raw_line or (i >= 2 and PRAGMA in lines[i - 2]):
                continue
            if any(marker in raw_line for marker in NON_RENDERED_MARKERS):
                continue
            line = raw_line
            if in_block_comment:
                close = line.find("*/")
                if close == -1:
                    continue  # whole line still inside the block comment
                line = line[close + 2 :]
                in_block_comment = False
            for span in _code_spans(line):
                for pattern, label in (
                    (ADR_PATTERN, "ADR reference"),
                    (TICKET_PATTERN, "issue/PR reference"),
                ):
                    for m in pattern.finditer(span):
                        if not _enclosed_in_parens(span, m.start(), m.end()):
                            continue
                        findings.append((path, i, label, m.group(0)))
            # Did this line open a block comment that stays open past it?
            last_open = line.rfind("/*")
            last_close = line.rfind("*/")
            if last_open != -1 and (last_close == -1 or last_close < last_open):
                in_block_comment = True
    return findings


def main(argv: list[str]) -> int:
    args = argv[1:]
    paths = args if args else _tracked_files()
    findings = scan(paths)
    if not findings:
        return 0

    print("Frontend UI-text check FAILED — ADR/issue/PR references reach a rendered string:\n")
    for path, line, label, hit in findings:
        print(f"  {path}:{line}  [{label}]  {hit!r}")
    print(
        "\nAn ADR/issue/PR number is meaningless to a user with no repo access — that context\n"
        "belongs in the PR description and commit message, not in a Tooltip/Alert/label/form-\n"
        "field string. Fix one of these ways:\n"
        "  • drop the parenthetical/reference from the user-facing string;\n"
        "  • if this really is a comment the scanner misread (e.g. an unusual multi-line JSX\n"
        f"    comment shape), add `{PRAGMA} <reason>` on the line or the one above."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
