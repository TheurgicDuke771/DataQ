"""Curated `docs/site/` pages exposed to MCP clients via `get_doc` (#1626).

Deliberately not a blanket scan of `docs/site/` — that tree also holds ADRs and
`architecture.md`, contributor/design-rationale content the audience asking
"what are DataQ's best practices?" isn't the reader of. The allowlist below is
the curation; scanning it live (rather than hand-listing slugs, the
`mcp_gates.GATES` lesson) means a renamed or deleted page is caught at import
time instead of silently drifting.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

#: Repo root — this file lives at backend/app/mcp/docs_catalog.py.
_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_DOCS_SITE: Final = _REPO_ROOT / "docs" / "site"

#: The curated allowlist (issue #1626): top-level pages plus every compliance
#: runbook/template. Relative to `docs/site/`, slash-separated.
_TOP_LEVEL_PAGES: Final = (
    "best-practices.md",
    "feature-matrix.md",
    "security.md",
    "getting-started.md",
    "mcp-setup.md",
    "mcp-honesty.md",
)
_COMPLIANCE_DIR: Final = "compliance"


class DocNotFoundError(Exception):
    """Raised by `read_page` for a `page` outside the curated allowlist."""


@lru_cache(maxsize=1)
def _scan() -> dict[str, Path]:
    """The curated pages that actually exist on disk, slug -> absolute path.

    Cached for the life of the process: `get_doc`'s advertised JSON-schema
    `enum` is built once, at import time, from this same function, so caching
    it is what keeps that enum and `read_page`'s validation from being able to
    diverge — every deployed image is immutable (docs/site is baked in at
    build time), so there is nothing on disk to re-scan for anyway.

    A page named in `_TOP_LEVEL_PAGES` that has been renamed or deleted is
    simply absent here rather than raised — `read_page` reports it as
    "not found" indistinguishably from a caller typo, which is the correct
    caller-facing behavior; a missing curated file is a docs-repo bug to catch
    in review/CI, not a 500 for whoever calls the tool next.
    """
    pages: dict[str, Path] = {}
    for name in _TOP_LEVEL_PAGES:
        path = _DOCS_SITE / name
        if path.is_file():
            pages[name.removesuffix(".md")] = path
    compliance_dir = _DOCS_SITE / _COMPLIANCE_DIR
    if compliance_dir.is_dir():
        for path in sorted(compliance_dir.glob("*.md")):
            pages[f"{_COMPLIANCE_DIR}/{path.stem}"] = path
    return pages


def list_pages() -> list[str]:
    """The current valid `page` slugs, sorted."""
    return sorted(_scan())


def read_page(page: str) -> str:
    """The verbatim Markdown content of a curated page.

    Raises `DocNotFoundError` (message lists the current valid slugs) for
    anything outside the allowlist — including a real `docs/site/` file that
    just isn't curated, e.g. an ADR.
    """
    pages = _scan()
    path = pages.get(page)
    if path is None:
        valid = ", ".join(sorted(pages)) or "(none found on disk)"
        raise DocNotFoundError(f"unknown page {page!r} — valid pages are: {valid}")
    return path.read_text(encoding="utf-8")
