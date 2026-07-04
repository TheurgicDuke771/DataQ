"""Drift guard: the ER diagram in docs/architecture.md tracks the real schema.

The diagram is hand-maintained (docs rule: update it in the same PR as any
model/migration change), so this pins the *table-level* contract both ways —
a model table missing from the diagram, or a diagram entity naming a table
that no longer exists, fails CI. Column-level sync stays a review concern
(checking every attribute here would just duplicate the models file).

Pure-unit: reads the markdown + `Base.metadata`; no DB.
"""

import re
from pathlib import Path

from backend.app.db import models
from backend.app.db.base import Base

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARCHITECTURE_MD = _REPO_ROOT / "docs" / "architecture.md"

# An entity definition inside the erDiagram block: `    users {`
_ENTITY_RE = re.compile(r"^\s*(\w+)\s*\{", re.MULTILINE)
_ER_BLOCK_RE = re.compile(r"```mermaid\s*\nerDiagram\n(.*?)```", re.DOTALL)


def _er_diagram_entities() -> set[str]:
    match = _ER_BLOCK_RE.search(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert match, "docs/architecture.md has no ```mermaid erDiagram``` block"
    return set(_ENTITY_RE.findall(match.group(1)))


def test_models_module_exports_every_mapped_table() -> None:
    # Sanity for the guard itself: the metadata the tests compare against is
    # populated by importing the models module.
    assert models.Base is Base
    assert Base.metadata.tables, "Base.metadata is empty — models not registered"


def test_every_table_appears_in_the_er_diagram() -> None:
    missing = set(Base.metadata.tables) - _er_diagram_entities()
    assert not missing, (
        f"tables missing from the docs/architecture.md ER diagram: {sorted(missing)} — "
        "update the diagram in the same PR as the model/migration change"
    )


def test_er_diagram_has_no_stale_tables() -> None:
    stale = _er_diagram_entities() - set(Base.metadata.tables)
    assert not stale, (
        f"docs/architecture.md ER diagram names tables that no longer exist: {sorted(stale)} — "
        "remove them from the diagram"
    )
