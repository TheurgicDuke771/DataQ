#!/usr/bin/env python3
"""Changelog fragments: one file per change in changelog.d/, assembled at release.

    changelog.py lint                       every fragment is well-formed
    changelog.py render-unreleased          print the "## Unreleased" section (stdout)
    changelog.py assemble --version X.Y.Z --date YYYY-MM-DD
                                            move the fragments into a new release section
    changelog.py check --base <ref> [--allow-none]
                                            a diff touching user-facing paths adds a fragment

Fragment grammar: `<number>.<section>.md` or `<number>-<n>.<section>.md`, where <number> is
the issue (or PR) the change belongs to, <n> a counter for a second fragment of the same
number, and <section> one of breaking | security | added | changed | fixed. The body is one
bullet's text without the leading "- " (continuation lines unindented). Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "docs" / "site" / "reference" / "changelog.md"
MARKER = "<!-- changelog.d:unreleased -->"

SECTIONS = ("breaking", "security", "added", "changed", "fixed")
SECTION_TITLES = {
    "breaking": "Breaking",
    "security": "Security",
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
}
FRAGMENT_NAME = re.compile(
    r"^(?P<number>[1-9]\d*)(?:-(?P<suffix>[1-9]\d*))?\.(?P<section>[a-z]+)\.md$"
)
NON_FRAGMENTS = {"README.md"}
EMPTY_UNRELEASED = "_No changes recorded since the last release._"

# The fragments ARE published text, but live outside docs/site/ where the rule-41 guard
# scans, so its patterns are applied here (loaded from the guard — one copy).
_DOCS_GUARD = ROOT / "scripts" / "check-docs-references.py"
_PUBLISHED_TEXT_RULES = (
    ("ISSUE_OR_PR_SHORTHAND", "issue/PR shorthand"),
    ("MERMAID_ESCAPED_SHORTHAND", "issue/PR shorthand (Mermaid-escaped)"),
    ("ISSUE_OR_PR_LINK", "issue/PR link"),
    ("UNPUBLISHED_DOC_LINK", "link to an unpublished internal doc"),
)


def _published_text_problem(text: str) -> str | None:
    spec = importlib.util.spec_from_file_location("dataq_check_docs_references", _DOCS_GUARD)
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    for attr, label in _PUBLISHED_TEXT_RULES:
        hit = getattr(guard, attr).search(text)
        if hit:
            return f"{label} {hit.group(0)!r}"
    return None


USER_FACING_PREFIXES = (
    "backend/app/",
    "frontend/src/",
    "packages/",
    "integrations/",
    "deploy/",
    "docs/site/guides/",
    "docs/site/get-started/",
    "docs/site/reference/",
)
NOT_USER_FACING = re.compile(r"(^|/)(__tests__/|.*\.(test|spec|stories)\.[tj]sx?$)")
NOT_USER_FACING_FILES = {"docs/site/reference/changelog.md"}


@dataclass(frozen=True)
class Fragment:
    path: Path
    number: int
    suffix: int
    section: str
    body: str

    @property
    def sort_key(self) -> tuple[int, int]:
        return (-self.number, self.suffix)


class FragmentError(ValueError):
    pass


def parse_name(name: str) -> tuple[int, int, str]:
    m = FRAGMENT_NAME.match(name)
    if not m:
        raise FragmentError(
            f"{name}: expected <number>.<section>.md or <number>-<n>.<section>.md "
            f"(section one of {', '.join(SECTIONS)})"
        )
    section = m.group("section")
    if section not in SECTIONS:
        raise FragmentError(f"{name}: unknown section {section!r}; one of {', '.join(SECTIONS)}")
    return int(m.group("number")), int(m.group("suffix") or 0), section


def load_fragment(path: Path) -> Fragment:
    number, suffix, section = parse_name(path.name)
    body = path.read_text(encoding="utf-8").strip("\n")
    if not body.strip():
        raise FragmentError(f"{path.name}: empty fragment")
    if body.lstrip().startswith(("- ", "* ")):
        raise FragmentError(f"{path.name}: the body is the bullet text — drop the leading '- '")
    if body.lstrip().startswith("#"):
        raise FragmentError(f"{path.name}: a fragment is one bullet, not a heading")
    problem = _published_text_problem(body)
    if problem:
        raise FragmentError(
            f"{path.name}: {problem} — published text never cites an issue or PR nor links "
            "an internal doc (CONTRIBUTING rule 41); state the behaviour instead"
        )
    return Fragment(path, number, suffix, section, body)


def load_fragments(directory: Path = FRAGMENTS_DIR) -> list[Fragment]:
    if not directory.is_dir():
        raise FragmentError(f"{directory} is missing")
    fragments = []
    for path in sorted(directory.iterdir()):
        if path.name in NON_FRAGMENTS or path.name.startswith("."):
            continue
        if path.is_dir():
            raise FragmentError(f"{path.name}: changelog.d holds files only")
        fragments.append(load_fragment(path))
    return fragments


def _bullet(body: str) -> str:
    first, *rest = body.split("\n")
    return "\n".join([f"- {first}", *(f"  {ln}" if ln.strip() else "" for ln in rest)])


def render_sections(fragments: list[Fragment]) -> str:
    """The '### Section' blocks in release order, each a tight bullet list."""
    out: list[str] = []
    for section in SECTIONS:
        chosen = sorted((f for f in fragments if f.section == section), key=lambda f: f.sort_key)
        if not chosen:
            continue
        out.append(
            f"### {SECTION_TITLES[section]}\n\n" + "\n".join(_bullet(f.body) for f in chosen)
        )
    return "\n\n".join(out)


def render_unreleased(fragments: list[Fragment]) -> str:
    body = render_sections(fragments) or EMPTY_UNRELEASED
    return f"## Unreleased\n\n{body}"


def _split_at_marker(markdown: str) -> tuple[str, str]:
    """(everything through the marker line, the rest from the next release heading on).

    A previously rendered Unreleased block between the two is dropped, which is what makes
    rendering idempotent and lets assemble run on a page that was previewed in place.
    """
    if MARKER not in markdown:
        raise FragmentError(f"{CHANGELOG.name} has no {MARKER} line")
    head, _, rest = markdown.partition(MARKER)
    tail = rest.lstrip("\n")
    if tail.startswith("## Unreleased"):
        cut = re.search(r"^## (?!Unreleased)", tail, flags=re.MULTILINE)
        tail = tail[cut.start() :] if cut else ""
    return head + MARKER, tail


def inject_unreleased(markdown: str, fragments: list[Fragment]) -> str:
    """The changelog page with the marker's section rendered (the mkdocs hook)."""
    head, tail = _split_at_marker(markdown)
    return f"{head}\n\n{render_unreleased(fragments)}\n\n{tail}"


def assemble(
    version: str, date: str, changelog: Path = CHANGELOG, directory: Path = FRAGMENTS_DIR
) -> str:
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", version):
        raise FragmentError(f"version {version!r} is not X.Y.Z")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise FragmentError(f"date {date!r} is not YYYY-MM-DD")
    version = version if version.startswith("v") else f"v{version}"
    fragments = load_fragments(directory)
    if not fragments:
        raise FragmentError(f"nothing to assemble: {directory.name} holds no fragments")
    head, tail = _split_at_marker(changelog.read_text(encoding="utf-8"))
    heading = f"## {version} — {date}"
    if re.search(rf"^## {re.escape(version)}\b", tail, flags=re.MULTILINE):
        raise FragmentError(f"{changelog.name} already has a {version} section")
    section = f"{heading}\n\n{render_sections(fragments)}"
    changelog.write_text(f"{head}\n\n{section}\n\n{tail}", encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()
    return heading


def is_user_facing(path: str) -> bool:
    if path in NOT_USER_FACING_FILES or NOT_USER_FACING.search(path):
        return False
    return path.startswith(USER_FACING_PREFIXES)


def changed_files(base: str, cwd: Path = ROOT) -> list[tuple[str, str]]:
    """(status, path) for every file the branch changed since it left `base`."""
    out = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-status", "--no-renames", f"{base}...HEAD"],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        rows.append((status[:1], path))
    return rows


def check(base: str, allow_none: bool, cwd: Path = ROOT) -> list[str]:
    """Problems with the branch's changelog bookkeeping; empty means it passes."""
    changes = changed_files(base, cwd)
    added = [p for s, p in changes if s in "AM" and p.startswith("changelog.d/")]
    problems = []
    for path in added:
        name = Path(path).name
        if name in NON_FRAGMENTS:
            continue
        try:
            load_fragment(cwd / path)
        except FragmentError as exc:
            problems.append(str(exc))
    fragments = [p for p in added if Path(p).name not in NON_FRAGMENTS]
    user_facing = [p for _, p in changes if is_user_facing(p)]
    if user_facing and not fragments and not allow_none:
        shown = ", ".join(user_facing[:5]) + (" …" if len(user_facing) > 5 else "")
        problems.append(
            f"user-facing change without a changelog fragment ({shown}): add "
            "changelog.d/<issue>.<section>.md, or label the PR `no-changelog`"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint")
    sub.add_parser("render-unreleased")
    p_assemble = sub.add_parser("assemble")
    p_assemble.add_argument("--version", required=True)
    p_assemble.add_argument("--date", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--base", required=True)
    p_check.add_argument("--allow-none", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "lint":
            fragments = load_fragments()
            print(f"changelog.d: {len(fragments)} fragment(s) OK")
        elif args.command == "render-unreleased":
            print(render_unreleased(load_fragments()))
        elif args.command == "assemble":
            heading = assemble(args.version, args.date)
            print(f"{CHANGELOG.relative_to(ROOT)}: added '{heading}'; fragments removed")
        elif args.command == "check":
            problems = check(args.base, args.allow_none)
            for problem in problems:
                print(f"changelog: {problem}")
            if problems:
                return 1
            print("changelog: OK")
    except (FragmentError, subprocess.CalledProcessError) as exc:
        print(f"changelog: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
