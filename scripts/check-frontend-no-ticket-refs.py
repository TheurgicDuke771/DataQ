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

This is a heuristic, not a parser: a `//` inside a string literal (e.g. a URL) is
treated as a comment starting early, which trades a rare false negative for never
false-flagging a legitimate comment. Good enough for a hook whose job is to catch
the common case before it ships, not to be a TypeScript AST.

Deliberate exceptions take an inline `frontend-ui-ok:` pragma **with a reason**, on
the line or the one above it.

Usage: `check-frontend-no-ticket-refs.py [files...]` — no args means every tracked
file under frontend/src.
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
SKIP_PREFIXES = ("frontend/src/test/", "frontend/src/tests/")

ADR_PATTERN = re.compile(r"\bADR[- ]?\d{2,4}\b", re.IGNORECASE)
# Issue/PR shorthand ("#1186"). A trailing hex-digit or another digit means it's
# not a bare decimal ticket number — bail before the boundary, so `#818cf8` or an
# 8-digit hex color never reach here at all.
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
    if path.startswith(SKIP_PREFIXES):
        return False
    if not path.endswith(SCAN_SUFFIXES):
        return False
    if path.endswith(SKIP_SUFFIXES):
        return False
    return True


def _looks_like_hex_color(text: str, start: int, end: int) -> bool:
    """`'#818cf8'` / `'#123456'` sitting inside a quoted string literal."""
    before = text[max(0, start - 1) : start]
    after_end = min(len(text), end + 1)
    after = text[end:after_end]
    return before in ("'", '"', "`") and after in ("'", '"', "`")


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
        for i, line in enumerate(lines, 1):
            if PRAGMA in line or (i >= 2 and PRAGMA in lines[i - 2]):
                continue
            stripped = line.lstrip()
            if in_block_comment:
                close = line.find("*/")
                if close == -1:
                    continue  # whole line still inside the block comment
                line = line[close + 2 :]
                in_block_comment = False
            # A `*` continuation line inside a JSDoc/JS-style block comment that
            # this scanner didn't see open (e.g. mid-multi-line JSX comment) —
            # already handled by in_block_comment above; this covers the common
            # `* prose` styling pre-commit sees line-by-line.
            if stripped.startswith("*") and not stripped.startswith("*/"):
                continue
            for span in _code_spans(line):
                for pattern, label in (
                    (ADR_PATTERN, "ADR reference"),
                    (TICKET_PATTERN, "issue/PR reference"),
                ):
                    for m in pattern.finditer(span):
                        if label == "issue/PR reference" and _looks_like_hex_color(
                            span, m.start(), m.end()
                        ):
                            continue
                        findings.append((path, i, label, m.group(0)))
            # Did this line open a block comment that stays open past it?
            spans_end = 0
            last_open = line.rfind("/*")
            last_close = line.rfind("*/")
            if last_open != -1 and (last_close == -1 or last_close < last_open):
                in_block_comment = True
            del spans_end
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
