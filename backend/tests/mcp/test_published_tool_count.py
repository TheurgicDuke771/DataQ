"""Published pages that state the MCP tool count must match the server (#2289)."""

import re
from pathlib import Path

import pytest

from backend.tests.support.mcp_gates import GATES

_SITE = Path(__file__).resolve().parents[3] / "docs" / "site"

# Pages that state the CURRENT count. ADRs and the changelog record historical counts on purpose.
_PAGES = (
    "guides/mcp-setup.md",
    "reference/glossary.md",
    "reference/feature-matrix.md",
    "get-started/ask-assistant.md",
)

_COUNT = re.compile(r"\b(\d+) (?:curated )?tools\b")


@pytest.mark.parametrize("page", _PAGES)
def test_a_published_tool_count_matches_the_server(page: str) -> None:
    counts = [int(n) for n in _COUNT.findall((_SITE / page).read_text(encoding="utf-8"))]

    assert counts, f"{page} no longer states a tool count; drop it from _PAGES"
    assert set(counts) == {len(GATES)}
