"""The secret-rename migration.

This script rewrites keys in **production Key Vault**, so its failure paths matter
more than its happy path. #954 is the reference incident: a partial credential
rotation left two Snowflake connections dead for three weeks. Every test here
asserts the same invariant — *whatever goes wrong, the connection still works*.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    SecretWriteError,
)
from backend.app.db.models import Connection
from backend.scripts.migrate_secret_names import MigrationError, _migrate_one


class FakeStore:
    """In-memory SecretStore with injectable faults at each step."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data: dict[str, str] = dict(initial or {})
        self.deleted: list[str] = []
        self.fail_set = False
        self.fail_verify: Exception | None = None
        self.fail_verify_on: str = ""
        self.corrupt_on_write = False

    def get(self, name: str) -> str:
        # Fires only on the NEW key, so it models a failed VERIFY rather than a
        # failed initial read — the earlier version tripped on step 1 and the test
        # passed against the wrong branch entirely.
        if self.fail_verify is not None and name == self.fail_verify_on:
            raise self.fail_verify
        if name not in self.data:
            raise SecretNotFoundError(f"{name} not set")
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        if self.fail_set:
            raise SecretWriteError("vault read-only")
        self.data[name] = "CORRUPTED" if self.corrupt_on_write else value

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.data.pop(name, None)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture()
def conn() -> Connection:
    cid = uuid.UUID("491f23e3-fd3a-4ff1-97d1-572f740ade3d")
    c = Connection(name="Finance Warehouse", type="snowflake", env="dev", config={})
    c.id = cid
    c.secret_ref = f"conn-{cid}"
    return c


OLD = "conn-491f23e3-fd3a-4ff1-97d1-572f740ade3d"
NEW = "conn-snowflake-finance-warehouse-dev-491f23e3"


def test_dry_run_changes_absolutely_nothing(conn: Connection) -> None:
    store = FakeStore({OLD: "p@ss"})
    session = FakeSession()
    old_ref, new_ref = _migrate_one(session, conn, store, apply=False)  # type: ignore[arg-type]
    assert (old_ref, new_ref) == (OLD, NEW)
    assert store.data == {OLD: "p@ss"}  # nothing written
    assert store.deleted == []  # nothing purged
    assert session.commits == 0  # nothing committed
    assert conn.secret_ref == OLD  # row untouched


def test_apply_copies_verifies_repoints_then_purges(conn: Connection) -> None:
    store = FakeStore({OLD: "p@ss"})
    session = FakeSession()
    _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert store.data == {NEW: "p@ss"}
    assert store.deleted == [OLD]
    assert conn.secret_ref == NEW
    assert session.commits == 1


def test_a_read_back_mismatch_aborts_before_the_row_is_repointed(conn: Connection) -> None:
    """The heart of it: if the new key does NOT hold the old value, the row must
    keep pointing at the old key — which still works — and the old key must NOT
    be purged. Trusting the write instead of checking it is how #954 happened."""
    store = FakeStore({OLD: "p@ss"})
    store.corrupt_on_write = True
    session = FakeSession()
    with pytest.raises(MigrationError, match="read-back mismatch") as exc:
        _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    # The new key is left written and unreferenced. It holds a real credential, so
    # the error MUST name it — otherwise nobody can find it to purge it, and the
    # module docstring's "logged by name for manual cleanup" promise is false.
    assert NEW in str(exc.value)
    assert conn.secret_ref == OLD  # still resolvable
    assert store.data[OLD] == "p@ss"  # original intact
    assert store.deleted == []  # nothing destroyed
    assert session.commits == 0


def test_a_write_failure_leaves_the_old_key_authoritative(conn: Connection) -> None:
    store = FakeStore({OLD: "p@ss"})
    store.fail_set = True
    session = FakeSession()
    with pytest.raises(MigrationError, match="could not write"):
        _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert conn.secret_ref == OLD
    assert store.data == {OLD: "p@ss"}
    assert store.deleted == []


def test_a_missing_old_key_is_reported_not_fabricated(conn: Connection) -> None:
    """The #1059 orphan case: the row points at a key that isn't there. The
    migration must refuse rather than mint an empty secret under the new name."""
    store = FakeStore({})  # old key absent
    session = FakeSession()
    with pytest.raises(MigrationError, match="old key absent"):
        _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert store.data == {}  # nothing created
    assert conn.secret_ref == OLD


def test_an_unavailable_store_is_retryable_not_destructive(conn: Connection) -> None:
    """A sealed vault must not be mistaken for 'no such secret' and must leave
    every side untouched so the run can simply be repeated."""

    class Sealed(FakeStore):
        def get(self, name: str) -> str:
            raise SecretStoreUnavailableError("vault sealed")

    store = Sealed({OLD: "p@ss"})
    session = FakeSession()
    with pytest.raises(MigrationError, match="retry later"):
        _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert conn.secret_ref == OLD
    assert store.deleted == []


def test_a_purge_failure_still_leaves_a_working_connection(conn: Connection) -> None:
    """Step 5 is fail-soft by the Protocol's contract. The result is a duplicated
    credential, not a broken connection — the row already points at the verified
    new key."""

    class UnpurgeableStore(FakeStore):
        def delete(self, name: str) -> None:
            return  # silently does nothing, as a fail-soft delete may

    store = UnpurgeableStore({OLD: "p@ss"})
    session = FakeSession()
    _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert conn.secret_ref == NEW
    assert store.data[NEW] == "p@ss"  # the connection resolves
    assert store.data[OLD] == "p@ss"  # leftover duplicate, by design


def test_verify_step_failure_does_not_repoint_the_row(conn: Connection) -> None:
    store = FakeStore({OLD: "p@ss"})
    session = FakeSession()
    store.fail_verify = SecretNotFoundError("gone")
    store.fail_verify_on = NEW
    with pytest.raises(MigrationError, match="unreadable after write") as exc:
        _migrate_one(session, conn, store, apply=True)  # type: ignore[arg-type]
    assert NEW in str(exc.value), "the abandoned key must be named"
    assert conn.secret_ref == OLD
    assert session.commits == 0


def test_a_db_commit_failure_is_per_connection_not_fatal(conn: Connection) -> None:
    """A commit error must become a MigrationError, or it escapes main() and the
    record of which keys were already renamed AND PURGED is lost — in a script whose
    whole point is that the vault can be reconciled afterwards."""

    class BadSession(FakeSession):
        def commit(self) -> None:
            raise RuntimeError("deadlock detected")

    store = FakeStore({OLD: "p@ss"})
    with pytest.raises(MigrationError, match="DB commit failed") as exc:
        _migrate_one(BadSession(), conn, store, apply=True)  # type: ignore[arg-type]
    assert NEW in str(exc.value)
    assert store.deleted == [], "the old key must survive a failed commit"
