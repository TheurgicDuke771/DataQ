"""Up/down test for the `c4e5a6b7d8f9_add_incidents` migration."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect

from backend.tests.support.migrations import load_migration, migration_ops

_MIGRATION = "c4e5a6b7d8f9_add_incidents.py"


def _has_incidents(connection: Any) -> bool:
    return bool(inspect(connection).has_table("incidents"))


def _has_auto_resolve(connection: Any) -> bool:
    cols = {c["name"] for c in inspect(connection).get_columns("suite_notifications")}
    return "auto_resolve_incidents" in cols


def _has_active_index(connection: Any) -> bool:
    if not _has_incidents(connection):
        return False
    names = {ix["name"] for ix in inspect(connection).get_indexes("incidents")}
    return "uq_incidents_active_asset_check" in names


def test_revision_chain() -> None:
    module = load_migration(_MIGRATION)
    assert module.revision == "c4e5a6b7d8f9"
    assert module.down_revision == "f0a1b2c3d4e5"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (create) → down (drop). `create_all` (the fixture) already
    made the table + column, so the sequence starts by dropping them.

    `notifications.incident_id` (#1687) references `incidents`, so — as a real downgrade
    chain would — that later revision is stepped down first and back up at the end.
    """
    module = load_migration(_MIGRATION)
    notifications = load_migration("c38847820d74_add_notifications_feed.py")
    connection = db_session.connection()
    with migration_ops(connection):
        assert _has_incidents(connection)  # baseline from create_all
        assert _has_active_index(connection)
        assert _has_auto_resolve(connection)

        notifications.downgrade()
        module.downgrade()
        assert not _has_incidents(connection)
        assert not _has_auto_resolve(connection)

        module.upgrade()
        assert _has_incidents(connection)
        assert _has_active_index(connection)
        assert _has_auto_resolve(connection)

        module.downgrade()
        assert not _has_incidents(connection)
        module.upgrade()
        notifications.upgrade()
    db_session.rollback()  # keep the create_all schema intact for other tests
