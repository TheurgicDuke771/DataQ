"""MkDocs hook: the changelog page's "Unreleased" section is rendered from changelog.d/.

The committed page carries only a marker line, so no PR edits the page between releases
and two PRs that each add an entry never conflict. The section is built here, at build
time, from whatever fragments exist — there is nothing to re-run and nothing to go stale.
A malformed fragment fails `mkdocs build --strict` the same way it fails pre-commit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "changelog.py"
_PAGE = "reference/changelog.md"


def _changelog_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dataq_changelog", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["dataq_changelog"] = module
    spec.loader.exec_module(module)
    return module


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    if page.file.src_uri != _PAGE:
        return markdown
    changelog = _changelog_module()
    return str(changelog.inject_unreleased(markdown, changelog.load_fragments()))
