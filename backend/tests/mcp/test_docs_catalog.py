"""Unit tests for the curated docs catalog (no DB, no network) — #1626."""

from backend.app.mcp import docs_catalog


def test_list_pages_finds_the_top_level_pages() -> None:
    pages = docs_catalog.list_pages()
    for slug in (
        "best-practices",
        "feature-matrix",
        "security",
        "getting-started",
        "mcp-setup",
        "mcp-honesty",
        "evidence-card",
    ):
        assert slug in pages


def test_list_pages_finds_all_five_compliance_pages() -> None:
    """A glob/path bug here would silently drop pages rather than error — this
    is what catches that, not a hand-maintained slug list drifting (#1626).
    """
    pages = set(docs_catalog.list_pages())
    for slug in (
        "compliance/breach-notification-runbook",
        "compliance/data-subject-rights-runbook",
        "compliance/dpa-baa-templates",
        "compliance/dpia-input-sheet",
        "compliance/sub-processors",
    ):
        assert slug in pages


def test_list_pages_excludes_adrs_and_architecture() -> None:
    """The curated allowlist deliberately excludes contributor-facing docs —
    scanning a specific set of files/dirs, not a blanket docs/site walk.
    """
    pages = set(docs_catalog.list_pages())
    assert not any(p.startswith("adr/") for p in pages)
    assert "architecture" not in pages
    assert "deployment" not in pages


def test_read_page_returns_verbatim_content() -> None:
    content = docs_catalog.read_page("best-practices")
    assert content  # non-empty
    assert content == (docs_catalog._DOCS_SITE / "best-practices.md").read_text(encoding="utf-8")


def test_read_page_compliance_subpath() -> None:
    content = docs_catalog.read_page("compliance/sub-processors")
    assert content


def test_read_page_unknown_slug_lists_the_current_valid_pages() -> None:
    try:
        docs_catalog.read_page("architecture")
    except docs_catalog.DocNotFoundError as exc:
        message = str(exc)
        assert "architecture" in message
        assert "best-practices" in message
        assert "compliance/sub-processors" in message
    else:
        raise AssertionError("expected DocNotFoundError")


def test_read_page_rejects_path_traversal() -> None:
    """Not in the allowlist, so it's refused like any other unknown slug —
    the scan never resolves a caller-supplied path, so there's nothing to
    traverse out of.
    """
    try:
        docs_catalog.read_page("../../../etc/passwd")
    except docs_catalog.DocNotFoundError:
        pass
    else:
        raise AssertionError("expected DocNotFoundError")
