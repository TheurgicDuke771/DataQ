"""Published pages that state the MCP tool count must match the server (#2289, #2503)."""

import html
import re
from pathlib import Path

from backend.tests.support.mcp_gates import GATES, tools_with_gate

_ROOT = Path(__file__).resolve().parents[3]
_SITE = _ROOT / "docs" / "site"
_MARKETING = _ROOT / "marketing" / "index.html"
_README = _ROOT / "README.md"

# "N-tool surface" is the deployment-parity wording for what a past E2E run covered.
_TOTAL = re.compile(
    r"\b(\d+) (?:(?:curated|MCP|`/mcp`) )*tools\b|\bThe (\d+) split\b|\b(\d+)-tool (?!surface)"
)
_SPLIT = re.compile(r"(\d+) read-only, (\d+) that change state,? (?:and )?(\d+)")
_READ_ONLY_GATES = ("read", "read:suite-optional", "suite:view", "incident:view", "contract:view")


def _published_pages() -> list[Path]:
    # ADRs and the changelog record the count as it stood when each entry was written.
    docs = [
        p
        for p in _SITE.rglob("*.md")
        if "adr" not in p.relative_to(_SITE).parts and p.name != "changelog.md"
    ]
    return [*docs, _MARKETING, _README]


def _text(page: Path) -> str:
    """The page's words: HTML tags and entities and Markdown bold out, whitespace collapsed."""
    raw = page.read_text(encoding="utf-8")
    if page.suffix == ".html":
        raw = html.unescape(re.sub(r"<[^>]+>", "", raw))
    return re.sub(r"\s+", " ", raw.replace("**", ""))


def test_the_landing_page_and_readme_are_scanned_and_state_a_count() -> None:
    for page in (_MARKETING, _README):
        assert page in _published_pages()
        assert _TOTAL.search(_text(page)), f"{page.name} states no count; drop it here"


def test_every_published_tool_count_matches_the_server() -> None:
    stale = {
        f"{page.relative_to(_ROOT)}: {n}"
        for page in _published_pages()
        for match in _TOTAL.findall(_text(page))
        if (n := int(next(filter(None, match)))) != len(GATES)
    }

    assert not stale


def test_every_published_split_adds_up_and_counts_the_read_only_tools() -> None:
    read_only = len(tools_with_gate(*_READ_ONLY_GATES))
    splits = [
        (page.relative_to(_ROOT), tuple(map(int, m)))
        for page in _published_pages()
        for m in _SPLIT.findall(_text(page))
    ]

    assert splits, "no page states the split any more; drop this test"
    assert [
        (page, split) for page, split in splits if split[0] != read_only or sum(split) != len(GATES)
    ] == []
