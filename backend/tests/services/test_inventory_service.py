"""The #919 inventory sync — opt-in gating, caps, fail-softness, and the sweep
interplay ADR 0040 leans on (last_seen advancement IS the lifecycle mechanism)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.secrets import SecretStore
from backend.app.db.models import Asset, Connection, User
from backend.app.services import asset_service, inventory_service
from backend.app.services.asset_identity import AssetIdentity
from backend.tests.support.fake_secret_store import FakeSecretStore


def _store() -> SecretStore:
    return FakeSecretStore(default="sekret")


def _user(db_session: Any) -> User:
    user: User | None = db_session.scalars(select(User)).first()
    if user is None:
        user = User(aad_object_id=f"aad-{uuid.uuid4().hex[:8]}", email="t@x.io")
        db_session.add(user)
        db_session.flush()
    return user


def _connection(db_session: Any, *, opted_in: bool, conn_type: str = "snowflake") -> Connection:
    config: dict[str, Any] = {"account": "ACC-1", "database": "DATAQ_DB"}
    if opted_in:
        config["inventory_sync"] = True
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=config,
        secret_ref="ref",
        created_by=_user(db_session).id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


class _FakeProvider:
    def __init__(self, identities: tuple[AssetIdentity, ...], fail: bool = False) -> None:
        self.identities = identities
        self.fail = fail
        self.seen_limit: int | None = None

    def enumerate_tables(
        self, conn: object, *, connection_config: dict[str, object], limit: int | None = None
    ) -> tuple[AssetIdentity, ...]:
        if self.fail:
            raise RuntimeError("warehouse unreachable")
        self.seen_limit = limit
        return self.identities[:limit] if limit is not None else self.identities


@contextmanager
def _fake_open_connection(connection: Connection, secret_store: Any) -> Any:
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeProvider]:
    """Wire the fake connection opener + a per-test provider registry."""
    providers: dict[str, _FakeProvider] = {}
    from backend.app.services import profile_service

    monkeypatch.setattr(profile_service, "_open_connection", _fake_open_connection)
    monkeypatch.setattr(
        inventory_service,
        "get_warehouse_lineage_provider",
        lambda conn_type: providers.get(conn_type),
    )
    return providers


def _idents(*names: str) -> tuple[AssetIdentity, ...]:
    return tuple(AssetIdentity(namespace="snowflake://ACC-1", name=n) for n in names)


class TestOptInAndSync:
    def test_only_opted_in_connections_sync(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        _connection(db_session, opted_in=False)

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        assert total == 1
        names = set(db_session.scalars(select(Asset.name)).all())
        assert names == {"DATAQ_DB.REFERENCE.LOOKUP"}

    def test_synced_asset_carries_connection_provenance(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        conn = _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        asset = db_session.scalars(select(Asset)).one()
        assert asset.connection_id == conn.id
        assert asset.env == "dev"

    def test_one_broken_connection_never_starves_the_rest(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.T"))
        wired["unity_catalog"] = _FakeProvider((), fail=True)
        _connection(db_session, opted_in=True)
        uc = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        uc.config = {**uc.config, "inventory_sync": True}
        db_session.flush()

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        assert total == 1  # the healthy connection synced; the broken one logged

    def test_unregistered_type_is_loud_not_silent(self, db_session: Any, wired: Any) -> None:
        conn = _connection(db_session, opted_in=True)
        with pytest.raises(ValueError, match="no table enumerator"):
            inventory_service.sync_connection_inventory(
                db_session, connection=conn, secret_store=_store()
            )


class TestCap:
    def test_overflow_syncs_first_cap_in_order_and_logs(
        self, db_session: Any, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASSET_INVENTORY_MAX_TABLES", "2")
        from backend.app.core.config import get_settings

        get_settings.cache_clear()
        try:
            wired["snowflake"] = _FakeProvider(
                _idents("DATAQ_DB.A.T1", "DATAQ_DB.A.T2", "DATAQ_DB.A.T3")
            )
            conn = _connection(db_session, opted_in=True)
            synced = inventory_service.sync_connection_inventory(
                db_session, connection=conn, secret_store=_store()
            )
            assert synced == 2
            # cap+1 requested so overflow is detectable, never silently absorbed
            assert wired["snowflake"].seen_limit == 3
            names = sorted(db_session.scalars(select(Asset.name)).all())
            assert names == ["DATAQ_DB.A.T1", "DATAQ_DB.A.T2"]
        finally:
            get_settings.cache_clear()


class TestOutcomeState:
    """#1104 — the sweep records its outcome ONTO the connection (mirroring the
    `lineage_last_*` pattern), so a grant failure becomes a fact about the
    connection rather than a line in App Insights, and a later success clears it."""

    def test_success_stamps_attempted_at_and_clears_error_state(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.T"))
        conn = _connection(db_session, opted_in=True)

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_attempted_at is not None
        assert conn.inventory_sync_last_error is None
        assert conn.inventory_sync_failing_since is None

    def test_failure_records_a_classified_error_and_failing_since(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["unity_catalog"] = _FakeProvider((), fail=True)
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        # Commit the setup row first: the sweep's failure branch rolls back any
        # in-flight partial write from the failed attempt itself, and (unlike prod,
        # where the connection is always an already-persisted row) an uncommitted
        # test fixture would be rolled back right along with it.
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_attempted_at is not None
        assert conn.inventory_sync_last_error is not None
        assert conn.inventory_sync_failing_since is not None
        # The raw exception text must never land on the connection.
        assert "warehouse unreachable" not in conn.inventory_sync_last_error

    def test_failing_since_holds_across_consecutive_failures(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["unity_catalog"] = _FakeProvider((), fail=True)
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        first_failing_since = conn.inventory_sync_failing_since
        first_attempted_at = conn.inventory_sync_last_attempted_at
        assert first_failing_since is not None
        assert first_attempted_at is not None

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        # attempted_at advances on every tick, but the START of the streak doesn't.
        assert conn.inventory_sync_last_attempted_at is not None
        assert conn.inventory_sync_last_attempted_at >= first_attempted_at
        assert conn.inventory_sync_failing_since == first_failing_since

    def test_a_subsequent_success_clears_the_failure_state(
        self, db_session: Any, wired: Any
    ) -> None:
        provider = _FakeProvider((), fail=True)
        wired["unity_catalog"] = provider
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_last_error is not None
        assert conn.inventory_sync_failing_since is not None

        # The grant issue is fixed; the next tick enumerates successfully.
        provider.fail = False
        provider.identities = _idents("DATAQ_DB.A.T")
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_error is None
        assert conn.inventory_sync_failing_since is None

    def test_non_opted_in_connection_never_gets_outcome_state(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.T"))
        conn = _connection(db_session, opted_in=False)

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_attempted_at is None
        assert conn.inventory_sync_last_error is None
        assert conn.inventory_sync_failing_since is None


class TestZeroTableEnumeration:
    """#1242 — a SUCCESSFUL sync that enumerates zero tables must be honestly
    distinguishable from "never synced" and from "synced, N>0", and a DROP from
    N>0 to 0 (the privilege-loss/dropped-database signal) must be flagged —
    without treating an always-empty database as a failure."""

    def test_never_synced_has_no_table_count(self, db_session: Any, wired: Any) -> None:
        conn = _connection(db_session, opted_in=True)
        db_session.commit()

        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count is None
        assert conn.inventory_sync_zero_since is None

    def test_zero_row_success_is_healthy_but_recorded(self, db_session: Any, wired: Any) -> None:
        """An empty-by-design database enumerating zero tables must NOT read as a
        sync failure (no error, no failing_since) — but the zero must be visible
        as its own recorded state, distinguishable from never having synced."""
        wired["snowflake"] = _FakeProvider(())
        conn = _connection(db_session, opted_in=True)

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        assert total == 0
        db_session.refresh(conn)
        assert conn.inventory_sync_last_attempted_at is not None
        assert conn.inventory_sync_last_error is None
        assert conn.inventory_sync_failing_since is None
        assert conn.inventory_sync_last_table_count == 0
        # Never having seen N>0 before means this is the neutral "always empty"
        # state, not the flagged drop signal.
        assert conn.inventory_sync_zero_since is None

    def test_nonzero_sync_records_the_count(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.T1", "DATAQ_DB.A.T2"))
        conn = _connection(db_session, opted_in=True)

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count == 2
        assert conn.inventory_sync_zero_since is None

    def test_drop_from_nonzero_to_zero_is_flagged(self, db_session: Any, wired: Any) -> None:
        """This is the privilege-loss/dropped-database signal — worth flagging,
        unlike a database that has always been empty."""
        provider = _FakeProvider(_idents("DATAQ_DB.A.T1"))
        wired["snowflake"] = provider
        conn = _connection(db_session, opted_in=True)

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count == 1
        assert conn.inventory_sync_zero_since is None

        # The role loses its grant (or the table is dropped) — the enumeration
        # query still runs fine, it just answers with nothing now.
        provider.identities = ()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_error is None  # still not a "failure"
        assert conn.inventory_sync_last_table_count == 0
        assert conn.inventory_sync_zero_since is not None

    def test_zero_since_holds_across_consecutive_zero_ticks(
        self, db_session: Any, wired: Any
    ) -> None:
        """Like `inventory_sync_failing_since`, the streak START must not walk
        forward on every subsequent zero tick."""
        provider = _FakeProvider(_idents("DATAQ_DB.A.T1"))
        wired["snowflake"] = provider
        conn = _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        provider.identities = ()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        first_zero_since = conn.inventory_sync_zero_since
        assert first_zero_since is not None

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_zero_since == first_zero_since

    def test_recovery_above_zero_clears_the_flag(self, db_session: Any, wired: Any) -> None:
        provider = _FakeProvider(_idents("DATAQ_DB.A.T1"))
        wired["snowflake"] = provider
        conn = _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        provider.identities = ()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_zero_since is not None

        # The grant is restored (or the table recreated) — the next sync sees it.
        provider.identities = _idents("DATAQ_DB.A.T1")
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count == 1
        assert conn.inventory_sync_zero_since is None

    def test_a_failed_attempt_leaves_the_table_count_untouched(
        self, db_session: Any, wired: Any
    ) -> None:
        """A failed attempt has no count to report — it must not clobber the last
        KNOWN count (or the zero-drop flag) with a non-answer."""
        provider = _FakeProvider(_idents("DATAQ_DB.A.T1"))
        wired["unity_catalog"] = provider
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count == 1

        provider.fail = True
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_error is not None
        # The count from the last SUCCESSFUL sync survives the failed attempt.
        assert conn.inventory_sync_last_table_count == 1
        assert conn.inventory_sync_zero_since is None

    def test_opting_out_clears_the_table_count_state(self, db_session: Any, wired: Any) -> None:
        provider = _FakeProvider(_idents("DATAQ_DB.A.T1"))
        wired["snowflake"] = provider
        conn = _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        provider.identities = ()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_zero_since is not None

        conn.config = {k: v for k, v in conn.config.items() if k != "inventory_sync"}
        db_session.commit()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_table_count is None
        assert conn.inventory_sync_zero_since is None


class TestOutcomeRobustness:
    """The bookkeeping must never be able to kill the sweep (#1227 review).

    `sync_asset_inventory` documents "one broken connection never starves the
    rest" — but the outcome write itself sat OUTSIDE that guarantee: an
    unguarded `session.commit()` in the failure branch, a read-modify-write with
    no row lock, and attribute reads on an ORM instance the preceding rollback
    had already expired. Each of those turns a per-connection problem into a
    per-SWEEP one, silently skipping every remaining connection.
    """

    def test_the_sweep_survives_a_failing_bookkeeping_commit(
        self, db_session: Any, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient DB error on the outcome commit must not abort the sweep."""
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.SNOW"))
        wired["unity_catalog"] = _FakeProvider(_idents("DATAQ_DB.A.UC"))
        target = _connection(db_session, opted_in=True)
        other = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        real_commit = db_session.commit
        blown: list[bool] = []

        def flaky_commit() -> None:
            # Fire ONLY on the bookkeeping write for `target` — identified by the
            # connection row being the dirty object — so the asset upsert's own
            # commit still lands and the assertion below is about the right commit.
            if not blown and any(
                isinstance(obj, Connection) and obj.id == target.id for obj in db_session.dirty
            ):
                blown.append(True)
                raise RuntimeError("transient failure committing the outcome")
            real_commit()

        monkeypatch.setattr(db_session, "commit", flaky_commit)
        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        monkeypatch.undo()

        assert blown, "the bookkeeping commit never failed — this test would prove nothing"
        # Both connections were still enumerated: the failure stayed local to the
        # bookkeeping of ONE connection.
        assert total == 2
        assert set(db_session.scalars(select(Asset.name)).all()) == {
            "DATAQ_DB.A.SNOW",
            "DATAQ_DB.A.UC",
        }
        # And the healthy sibling's outcome was still recorded.
        db_session.refresh(other)
        assert other.inventory_sync_last_attempted_at is not None

    def test_the_outcome_write_takes_a_row_lock(self, db_session: Any, wired: Any) -> None:
        """`inventory_sync_failing_since` is a read-modify-write, so it needs the same
        `FOR UPDATE` guard `orchestration_service.record_poll_failure` takes: two
        overlapping sweeps would otherwise both read NULL and both write their own
        "now", walking the START of a failure streak forward and under-reporting how
        long the connection has been broken.

        Asserted on the SQL Postgres actually receives, not on a kwarg or on the
        helper being called — a lock test that only checks we called the locking
        function proves nothing about the statement.
        """
        from sqlalchemy import event

        wired["unity_catalog"] = _FakeProvider((), fail=True)
        _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            statements.append(statement)

        event.listen(db_session.bind, "before_cursor_execute", record)
        try:
            inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        finally:
            event.remove(db_session.bind, "before_cursor_execute", record)

        locking = [
            s
            for s in statements
            if "FOR UPDATE" in s.upper() and "FROM connections" in s.replace("\n", " ")
        ]
        assert locking, (
            "the outcome write never issued a locking SELECT — "
            f"failing_since can be lost-updated by a concurrent sweep. Saw: {statements}"
        )

    def test_a_connection_deleted_mid_sweep_does_not_abort_it(
        self, db_session: Any, wired: Any
    ) -> None:
        """Connection deletion mid-sweep is a real, reachable path — and the failure
        branch rolls the session back, which EXPIRES every attribute on every
        instance the loop is holding. Reading `connection.type` (or re-reading the
        opt-in config) after that raises `ObjectDeletedError` for a row that is gone,
        taking the whole beat task with it."""
        from sqlalchemy import delete as sa_delete

        doomed_a = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        doomed_b = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        healthy = _connection(db_session, opted_in=True)
        db_session.commit()

        class _SelfDeletingProvider:
            """Deletes BOTH UC connections, then fails — so whichever the sweep
            reaches first exercises the "row vanished before the outcome write"
            path and the other exercises "vanished before we even fetched it"."""

            def enumerate_tables(self, conn: object, **kwargs: Any) -> tuple[AssetIdentity, ...]:
                # `synchronize_session=False` on purpose: it reproduces the PROD
                # shape, where the delete happens in someone else's session (an
                # admin removing the connection through the API) and ours is left
                # holding a persistent-but-expired instance. The default
                # ("auto") would EXPUNGE the objects here, and a detached
                # instance keeps its last-loaded values — so `connection.type`
                # would answer happily from memory and the test would pass
                # against the very bug it exists to catch.
                db_session.execute(
                    sa_delete(Connection)
                    .where(Connection.id.in_([doomed_a.id, doomed_b.id]))
                    .execution_options(synchronize_session=False)
                )
                db_session.commit()
                raise RuntimeError("warehouse unreachable")

        wired["unity_catalog"] = _SelfDeletingProvider()
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.STILL_HERE"))

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        assert total == 1, "the sweep did not reach the surviving connection"
        assert db_session.scalars(select(Asset.name)).all() == ["DATAQ_DB.A.STILL_HERE"]
        db_session.refresh(healthy)
        assert healthy.inventory_sync_last_attempted_at is not None

    def test_opting_out_clears_the_stale_outcome_state(self, db_session: Any, wired: Any) -> None:
        """State describes the LAST ATTEMPT; with the toggle off there are no more
        attempts, so it must go blank rather than freeze. Otherwise re-enabling the
        sync months later renders "failing since <old date>" for a sync that has not
        run since."""
        wired["unity_catalog"] = _FakeProvider((), fail=True)
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        db_session.refresh(conn)
        assert conn.inventory_sync_failing_since is not None

        conn.config = {k: v for k, v in conn.config.items() if k != "inventory_sync"}
        db_session.commit()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        db_session.refresh(conn)
        assert conn.inventory_sync_last_attempted_at is None
        assert conn.inventory_sync_last_error is None
        assert conn.inventory_sync_failing_since is None


class TestFailurePhase:
    """A permission-shaped failure is only a missing GRANT if it happened while the
    enumeration query was running (#1227 review). The classifier's markers are broad
    substrings, so a secret-store 403 or an expired token matches PERMISSION just as
    well — and telling an admin to grant SELECT on a system schema for one of those
    sends them to fix something that was never broken while the real fault stays
    undiagnosed."""

    def _reason(self, db_session: Any, conn: Connection) -> str:
        db_session.refresh(conn)
        reason = conn.inventory_sync_last_error
        assert reason is not None, "the sweep recorded no failure reason at all"
        return reason

    def test_a_grant_failure_during_enumeration_names_the_system_schema(
        self, db_session: Any, wired: Any
    ) -> None:
        class _DeniedProvider:
            def enumerate_tables(self, conn: object, **kwargs: Any) -> tuple[AssetIdentity, ...]:
                raise RuntimeError("Insufficient privileges to SELECT on system.information_schema")

        wired["unity_catalog"] = _DeniedProvider()
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        assert "information_schema" in self._reason(db_session, conn)

    def test_a_permission_failure_before_the_warehouse_is_touched_does_not(
        self, db_session: Any, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The credential never resolved — the warehouse was never asked anything, so
        no statement of ours was rejected and no grant can be the cause."""
        from backend.app.services import profile_service

        @contextmanager
        def _boom(connection: Connection, secret_store: Any) -> Any:
            raise RuntimeError("HTTP 403 reading the credential from the secret store")
            yield  # pragma: no cover - unreachable, keeps this a generator

        monkeypatch.setattr(profile_service, "_open_connection", _boom)
        wired["unity_catalog"] = _FakeProvider(())
        conn = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        db_session.commit()

        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        reason = self._reason(db_session, conn)
        assert reason, "the failure was not recorded at all"
        assert "information_schema" not in reason.lower()
        assert "grant select" not in reason.lower()


class TestSweepInterplay:
    """The ADR 0040 lifecycle claim, pinned: a synced table never becomes a sweep
    candidate while it exists; a dropped table freezes and ages out."""

    def test_sync_keeps_a_live_table_out_of_the_sweep(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # Backdate last_seen past retention — then a fresh sync tick runs.
        asset = db_session.scalars(select(Asset)).one()
        asset.last_seen = datetime.now(UTC) - timedelta(days=40)
        db_session.flush()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        swept = asset_service.sweep_orphan_assets(db_session, retention_days=30)
        assert swept == 0
        assert db_session.scalars(select(Asset)).one().name == "DATAQ_DB.REFERENCE.LOOKUP"

    def test_a_dropped_table_ages_out_through_the_existing_sweep(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # The table disappears from the warehouse: later syncs no longer return
        # it, so last_seen freezes...
        wired["snowflake"].identities = ()
        asset = db_session.scalars(select(Asset)).one()
        asset.last_seen = datetime.now(UTC) - timedelta(days=40)
        db_session.flush()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # ...and the existing #770 sweep retires it. No new mechanism.
        swept = asset_service.sweep_orphan_assets(db_session, retention_days=30)
        assert swept == 1
        assert db_session.scalars(select(Asset)).first() is None
