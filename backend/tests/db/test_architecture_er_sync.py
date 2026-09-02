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
_FK_LINE_RE = re.compile(r'^\s*\w+\s+(\w+)\s+(?:[A-Z]+,)*FK(?:,[A-Z]+)*\s+"([^"]*)"', re.MULTILINE)
_ENTITY_BLOCK_RE = re.compile(r"^\s*(\w+)\s*\{(.*?)^\s*\}", re.MULTILINE | re.DOTALL)
# A relationship line: `    suites |o--o{ llm_invocations : "context scope (SET NULL)"`.
_RELATIONSHIP_RE = re.compile(r'^\s*(\w+)\s+[|o}{\-.]+\s+(\w+)\s*:\s*"([^"]*)"', re.MULTILINE)
_POLICIES = ("SET NULL", "CASCADE", "RESTRICT", "NO ACTION")


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


def _named_policy(comment: str) -> str | None:
    named = [p for p in _POLICIES if p in comment.upper()]
    return named[0] if named else None


def _documented_fk_policies() -> dict[tuple[str, str], str]:
    """(table, column) → the delete policy the diagram's FK comment names, if any."""
    match = _ER_BLOCK_RE.search(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert match
    documented: dict[tuple[str, str], str] = {}
    for table, body in _ENTITY_BLOCK_RE.findall(match.group(1)):
        for column, comment in _FK_LINE_RE.findall(body):
            policy = _named_policy(comment)
            if policy:
                documented[(table, column)] = policy
    return documented


def _documented_relationship_policies() -> list[tuple[str, str, str]]:
    """(parent, child, policy) for every relationship line whose label names one."""
    match = _ER_BLOCK_RE.search(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert match
    out = []
    for parent, child, label in _RELATIONSHIP_RE.findall(match.group(1)):
        policy = _named_policy(label)
        if policy:
            out.append((parent, child, policy))
    return out


def _actual_policy(fk: object) -> str:
    ondelete = getattr(fk, "ondelete", None)
    # Postgres's default is NO ACTION; the diagram may not call that RESTRICT.
    return (ondelete or "NO ACTION").upper()


def test_er_diagram_fk_delete_policies_match_the_models() -> None:
    """A quoted CASCADE / SET NULL / RESTRICT on an FK line is a claim about what deleting
    the parent does. #1806: the diagram said `llm_invocations.suite_id` cascades while the
    model and migration SET NULL — the wrong direction for an audit/cost record, and
    invisible to the table-name checks above.
    """
    documented = _documented_fk_policies()
    assert len(documented) >= 24, f"FK-line regex drift? parsed only {len(documented)}"
    drift: list[str] = []
    for (table, column), policy in documented.items():
        fks = list(Base.metadata.tables[table].columns[column].foreign_keys)
        actual = _actual_policy(fks[0]) if fks else "NOT AN FK"
        if actual != policy:
            drift.append(f"{table}.{column}: diagram says {policy}, model says {actual}")
    assert not drift, "\n".join(drift)


def test_er_diagram_relationship_delete_policies_match_the_models() -> None:
    """The relationship lines repeat the policy in their label; #1806's second half was
    exactly such a line. A labelled policy must match SOME FK from child to parent.
    """
    documented = _documented_relationship_policies()
    assert len(documented) >= 30, f"relationship regex drift? parsed only {len(documented)}"
    drift: list[str] = []
    for parent, child, policy in documented:
        actual = {
            _actual_policy(fk)
            for column in Base.metadata.tables[child].columns
            for fk in column.foreign_keys
            if fk.column.table.name == parent
        }
        if policy not in actual:
            drift.append(f"{parent} → {child}: diagram says {policy}, model says {sorted(actual)}")
    assert not drift, "\n".join(drift)
