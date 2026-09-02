"""Drift guard: the ER diagram in docs/site/architecture.md tracks the real schema."""

import re
from pathlib import Path

from backend.app.db import models
from backend.app.db.base import Base

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARCHITECTURE_MD = _REPO_ROOT / "docs" / "site" / "architecture.md"

# An entity definition inside the erDiagram block: `    users {`
_ENTITY_RE = re.compile(r"^\s*(\w+)\s*\{", re.MULTILINE)
_ER_BLOCK_RE = re.compile(r"```mermaid\s*\nerDiagram\n(.*?)```", re.DOTALL)
# An FK attribute line inside an entity block whose free-text comment names a delete
# policy: `uuid suite_id FK "SET NULL - the record outlives its scope"`.
_FK_LINE_RE = re.compile(r'^\s*\w+\s+(\w+)\s+FK\s+"([^"]*)"', re.MULTILINE)
_ENTITY_BLOCK_RE = re.compile(r"^\s*(\w+)\s*\{(.*?)^\s*\}", re.MULTILINE | re.DOTALL)
_POLICIES = ("SET NULL", "CASCADE", "RESTRICT")


def _er_diagram_entities() -> set[str]:
    match = _ER_BLOCK_RE.search(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert match, "docs/site/architecture.md has no ```mermaid erDiagram``` block"
    return set(_ENTITY_RE.findall(match.group(1)))


def test_models_module_exports_every_mapped_table() -> None:
    # Sanity for the guard itself: the metadata the tests compare against is
    # populated by importing the models module.
    assert models.Base is Base
    assert Base.metadata.tables, "Base.metadata is empty — models not registered"


def test_every_table_appears_in_the_er_diagram() -> None:
    missing = set(Base.metadata.tables) - _er_diagram_entities()
    assert not missing, (
        f"tables missing from the docs/site/architecture.md ER diagram: {sorted(missing)} — "
        "update the diagram in the same PR as the model/migration change"
    )


def test_er_diagram_has_no_stale_tables() -> None:
    stale = _er_diagram_entities() - set(Base.metadata.tables)
    assert not stale, (
        f"docs/site/architecture.md ER diagram names tables that no longer exist: "
        f"{sorted(stale)} — remove them from the diagram"
    )


def _documented_fk_policies() -> dict[tuple[str, str], str]:
    """(table, column) → the delete policy the diagram's FK comment names, if any."""
    match = _ER_BLOCK_RE.search(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert match
    documented: dict[tuple[str, str], str] = {}
    for table, body in _ENTITY_BLOCK_RE.findall(match.group(1)):
        for column, comment in _FK_LINE_RE.findall(body):
            named = [p for p in _POLICIES if p in comment.upper()]
            if named:
                documented[(table, column)] = named[0]
    return documented


def test_er_diagram_fk_delete_policies_match_the_models() -> None:
    """A quoted CASCADE / SET NULL on an FK line is a claim about what deleting the parent
    does. #1806: the diagram said `llm_invocations.suite_id` cascades while the model and
    migration SET NULL — the wrong direction for an audit/cost record, and invisible to
    the table-name checks above.
    """
    documented = _documented_fk_policies()
    assert documented, "no FK delete policies found in the ER diagram — regex drift?"
    drift: list[str] = []
    for (table, column), policy in documented.items():
        fks = list(Base.metadata.tables[table].columns[column].foreign_keys)
        actual = (fks[0].ondelete or "RESTRICT").upper() if fks else "NOT AN FK"
        if actual != policy:
            drift.append(f"{table}.{column}: diagram says {policy}, model says {actual}")
    assert not drift, "\n".join(drift)
