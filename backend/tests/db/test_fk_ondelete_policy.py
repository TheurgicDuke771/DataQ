"""Every foreign key must state an `ondelete` policy — #1319, #541."""

from __future__ import annotations

from typing import Final

from backend.app.db.models import Base

#: Edges deliberately left without a database-level `ondelete`, each with the mechanism that handles
#: the delete instead.
_EXPLICIT_NO_ACTION: Final[dict[str, str]] = {
    "suites.connection_id": (
        "guarded at the service layer with a 409 while any suite runs against the "
        "connection (#753). A database policy here would be wrong in both "
        "directions: CASCADE would silently destroy suites (and their runs and "
        "results, #540) with a connection, and SET NULL would leave a suite that "
        "can never run and cannot say why."
    ),
}


def _edges_without_ondelete() -> dict[str, str]:
    """`table.column` → referenced table, for every FK with no `ondelete`."""
    found: dict[str, str] = {}
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.ondelete is None:
                    found[f"{table.name}.{column.name}"] = fk.target_fullname
    return found


def test_the_scan_sees_the_real_schema() -> None:
    """The guard's own guard: every assertion below is vacuously true against an
    empty metadata, so the enumeration is checked before it is trusted.
    """
    total = sum(1 for t in Base.metadata.sorted_tables for c in t.columns for _ in c.foreign_keys)
    assert total > 20, f"only {total} foreign keys found — the scan is not seeing the schema"


def test_every_foreign_key_declares_an_ondelete_policy() -> None:
    """A new FK must choose a policy or justify not having one."""
    undeclared = {
        edge: target
        for edge, target in _edges_without_ondelete().items()
        if edge not in _EXPLICIT_NO_ACTION
    }
    assert not undeclared, (
        f"these foreign keys have no `ondelete` and default to NO ACTION: {undeclared} — "
        "a delete of the parent will raise ForeignKeyViolation and surface as an "
        "unhandled 500. Choose CASCADE / SET NULL / RESTRICT on the column, or add the "
        "edge to _EXPLICIT_NO_ACTION with the service-layer mechanism that handles it "
        "instead. #1319."
    )


def test_the_exemption_list_carries_no_edges_that_now_have_a_policy() -> None:
    """A stale exemption is worse than a missing one: it documents a mechanism
    that may no longer exist, while the edge quietly behaves differently.
    """
    stale = set(_EXPLICIT_NO_ACTION) - set(_edges_without_ondelete())
    assert not stale, (
        f"these edges now declare an `ondelete` and no longer need an exemption: "
        f"{sorted(stale)} — remove them from _EXPLICIT_NO_ACTION"
    )


def test_every_exemption_states_its_mechanism() -> None:
    """An unexplained exemption is indistinguishable from the oversight this test
    exists to catch.
    """
    thin = {edge: why for edge, why in _EXPLICIT_NO_ACTION.items() if len(why.strip()) < 40}
    assert not thin, f"exemptions with no substantive mechanism: {sorted(thin)}"


def test_the_user_provenance_edges_are_set_null() -> None:
    """The three edges #1319 is about, asserted by name."""
    for table_name, column_name in (
        ("connections", "created_by"),
        ("suites", "created_by"),
        ("schedules", "created_by"),
    ):
        column = Base.metadata.tables[table_name].columns[column_name]
        fk = next(iter(column.foreign_keys))
        assert fk.ondelete == "SET NULL", f"{table_name}.{column_name} is {fk.ondelete}"
        assert column.nullable, (
            f"{table_name}.{column_name} is NOT NULL, so SET NULL would fail at "
            "delete time — the two must agree"
        )
