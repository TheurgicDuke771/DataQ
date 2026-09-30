"""Published pages that state the MCP tool count must match the server (#2289)."""

import re
from pathlib import Path

from backend.tests.support.mcp_gates import GATES, tools_with_gate

_SITE = Path(__file__).resolve().parents[3] / "docs" / "site"

# "N-tool surface" is the deployment-parity wording for what a past E2E run covered.
_TOTAL = re.compile(
    r"\b(\d+) (?:curated |MCP |`/mcp` )?tools\b|\bThe (\d+) split\b|\b(\d+)-tool (?!surface)"
)
_SPLIT = re.compile(r"(\d+) read-only, (\d+) that change state,? (?:and )?(\d+)")
_READ_ONLY_GATES = ("read", "read:suite-optional", "suite:view", "incident:view")


def _published_pages() -> list[Path]:
    # ADRs and the changelog record the count as it stood when each entry was written.
    return [
        p
        for p in _SITE.rglob("*.md")
        if "adr" not in p.relative_to(_SITE).parts and p.name != "changelog.md"
    ]


def test_every_published_tool_count_matches_the_server() -> None:
    stale = {
        f"{page.relative_to(_SITE)}: {n}"
        for page in _published_pages()
        for match in _TOTAL.findall(page.read_text(encoding="utf-8"))
        if (n := int(next(filter(None, match)))) != len(GATES)
    }

    assert not stale


def test_every_published_split_adds_up_and_counts_the_read_only_tools() -> None:
    read_only = len(tools_with_gate(*_READ_ONLY_GATES))
    splits = [
        (page.relative_to(_SITE), tuple(map(int, m)))
        for page in _published_pages()
        for m in _SPLIT.findall(page.read_text(encoding="utf-8").replace("**", ""))
    ]

    assert splits, "no page states the split any more; drop this test"
    assert [
        (page, split) for page, split in splits if split[0] != read_only or sum(split) != len(GATES)
    ] == []
