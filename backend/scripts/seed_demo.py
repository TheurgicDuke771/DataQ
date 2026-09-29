"""Seed the `demo` compose profile: a sample warehouse DataQ can actually run against (#1706).

The rest of the seed (`seed_dev`) gives the UI history to show, but its credentials are
placeholders, so nothing it creates can run. This fills the bundled `demo-warehouse`
PostgreSQL with a small shop dataset carrying deliberate defects, creates a read-only
reader for it, and connects DataQ to it through the same services the API uses: one
connection, two suites whose checks cover the main check kinds, an hourly schedule, and a
first run of each suite, so an evaluator signs in to real results.

Idempotent: the data is created once, the reader's password is rotated on every run (through
`reauth_connection`, so it lands in the secret store the same way a user's would), and a suite
that already has a run is not run again.

    python -m backend.scripts.seed_demo
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from sqlalchemy import URL, create_engine, select, text
from sqlalchemy.orm import Session

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _upsert_user,
)
from backend.app.core.config import get_settings
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Check, Connection, Run, Schedule, Suite, User
from backend.app.db.session import get_session
from backend.app.services import (
    check_service,
    connection_service,
    run_dispatch,
    schedule_service,
    suite_service,
)
from backend.scripts.seed_dev import _share_with_otp_operators

CONNECTION_NAME = "Demo warehouse (PostgreSQL)"
READER = "dataq_reader"
SCHEMA = "shop"

#: Each order row's defects are placed at fixed ids, so every check has a known outcome.
_DATA_SQL = """
CREATE SCHEMA shop;

CREATE TABLE shop.customers (
    customer_id integer PRIMARY KEY,
    email text,
    country text NOT NULL,
    created_at timestamptz NOT NULL
);
INSERT INTO shop.customers
SELECT
    i,
    CASE WHEN i % 97 = 0 THEN NULL ELSE 'customer' || i || '@example.com' END,
    (ARRAY['US', 'GB', 'DE', 'FR', 'IN', 'JP'])[1 + i % 6],
    now() - make_interval(days => 400 - i % 400)
FROM generate_series(1, 400) AS i;
-- Two customers share an email address.
UPDATE shop.customers SET email = 'customer7@example.com' WHERE customer_id = 107;

CREATE TABLE shop.orders (
    order_id integer NOT NULL,
    customer_id integer NOT NULL,
    customer_email text,
    status text NOT NULL,
    amount numeric(10, 2) NOT NULL,
    discount numeric(10, 2) NOT NULL,
    country text NOT NULL,
    ordered_at timestamptz NOT NULL,
    shipped_at timestamptz
);
INSERT INTO shop.orders
SELECT
    i,
    1 + i % 400,
    CASE WHEN i % 50 = 0 THEN NULL ELSE 'customer' || (1 + i % 400) || '@example.com' END,
    (ARRAY['pending', 'shipped', 'shipped', 'shipped', 'cancelled'])[1 + i % 5],
    round((20 + (i * 37) % 480)::numeric, 2),
    round(((i * 13) % 25)::numeric, 2),
    (ARRAY['US', 'GB', 'DE', 'FR', 'IN', 'JP'])[1 + i % 6],
    now() - make_interval(mins => i * 7),
    CASE WHEN i % 5 IN (1, 2, 3) THEN now() - make_interval(mins => i * 7) + interval '1 day' END
FROM generate_series(1, 2000) AS i;
-- The defects the demo checks find.
UPDATE shop.orders SET amount = -15.00 WHERE order_id IN (13, 1313);
UPDATE shop.orders SET status = 'refunded' WHERE order_id IN (21, 421, 821);
UPDATE shop.orders SET customer_email = 'not-an-email' WHERE order_id = 77;
UPDATE shop.orders SET discount = amount + 10 WHERE order_id IN (99, 999);
INSERT INTO shop.orders SELECT * FROM shop.orders WHERE order_id = 500;
"""

_SUITES: list[dict[str, Any]] = [
    {
        "name": "Shop orders (demo)",
        "description": "Orders in the bundled demo warehouse. Several checks fail on purpose.",
        "table": "orders",
        "schedule": "0 * * * *",
        "checks": [
            (
                "Order id is unique",
                "expectation",
                "expect_column_values_to_be_unique",
                {"column": "order_id"},
                {},
            ),
            (
                "Customer email is present",
                "expectation",
                "expect_column_values_to_not_be_null",
                {"column": "customer_email"},
                {"warn_threshold": 1, "fail_threshold": 5},
            ),
            (
                "Customer email looks like an email",
                "expectation",
                "expect_column_values_to_match_regex",
                {"column": "customer_email", "regex": "^[^@]+@[^@]+\\.[a-z]+$"},
                {},
            ),
            (
                "Amount is not negative",
                "expectation",
                "expect_column_values_to_be_between",
                {"column": "amount", "min_value": 0},
                {},
            ),
            (
                "Status is a known value",
                "expectation",
                "expect_column_values_to_be_in_set",
                {"column": "status", "value_set": ["pending", "shipped", "cancelled"]},
                {},
            ),
            (
                "Discount never exceeds the amount",
                "expectation",
                "unexpected_rows_expectation",
                {"unexpected_rows_query": "SELECT * FROM {batch} WHERE discount > amount"},
                {},
            ),
            (
                "Orders are fresh",
                "freshness",
                "monitor:freshness",
                {"column": "ordered_at"},
                {"warn_threshold": 24, "fail_threshold": 72},
            ),
            (
                "Order volume",
                "volume",
                "monitor:volume",
                {"min_rows": 1000, "max_rows": 100000},
                {},
            ),
            (
                "Average order value",
                "aggregate",
                "monitor:aggregate",
                {"aggregate": "mean", "column": "amount", "min_value": 50, "max_value": 500},
                {},
            ),
            ("Schema is unchanged", "schema_drift", "monitor:schema_drift", {}, {}),
            (
                "Row count is normal",
                "anomaly",
                "monitor:anomaly",
                {"target_metric": "row_count", "window": 14, "min_points": 5},
                {"warn_threshold": 2, "fail_threshold": 3},
            ),
        ],
    },
    {
        "name": "Shop customers (demo)",
        "description": "Customers in the bundled demo warehouse.",
        "table": "customers",
        "schedule": None,
        "checks": [
            (
                "Customer id is unique",
                "expectation",
                "expect_column_values_to_be_unique",
                {"column": "customer_id"},
                {},
            ),
            (
                "Email is unique",
                "expectation",
                "expect_column_values_to_be_unique",
                {"column": "email"},
                {},
            ),
            (
                "Email is present",
                "expectation",
                "expect_column_values_to_not_be_null",
                {"column": "email"},
                {"warn_threshold": 0, "fail_threshold": 5},
            ),
            (
                "Country is a supported market",
                "expectation",
                "expect_column_values_to_be_in_set",
                {"column": "country", "value_set": ["US", "GB", "DE", "FR", "IN", "JP"]},
                {},
            ),
        ],
    },
]


def _warehouse() -> dict[str, Any]:
    return {
        "host": os.environ.get("DEMO_WAREHOUSE_HOST", "demo-warehouse"),
        "port": int(os.environ.get("DEMO_WAREHOUSE_PORT", "5432")),
        "database": os.environ.get("DEMO_WAREHOUSE_DB", "shop"),
        "admin": os.environ.get("DEMO_WAREHOUSE_ADMIN", "demo"),
    }


def prepare_warehouse(wh: dict[str, Any]) -> tuple[bool, str]:
    """Create the data once and give the reader a fresh password. Returns (created, password)."""
    password = secrets.token_urlsafe(24)
    url = URL.create(
        "postgresql+psycopg2",
        username=wh["admin"],
        host=wh["host"],
        port=wh["port"],
        database=wh["database"],
    )
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            created = (
                conn.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": SCHEMA})
                is None
            )
            if created:
                # A raw cursor with no parameters: `%` in the script is modulo, not a placeholder.
                conn.connection.cursor().execute(_DATA_SQL)
            exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": READER})
            # READER and SCHEMA are constants; only the password is a value, bound client-side.
            verb = "ALTER" if exists else "CREATE"
            conn.exec_driver_sql(
                f"{verb} ROLE {READER} LOGIN PASSWORD %(password)s", {"password": password}
            )
            conn.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {READER}; "
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {READER}"
            )
    finally:
        engine.dispose()
    return created, password


def _connection(session: Session, owner: User, wh: dict[str, Any], password: str) -> Connection:
    store = get_secret_store()
    existing = session.scalar(select(Connection).where(Connection.name == CONNECTION_NAME))
    if existing is not None:
        connection_service.reauth_connection(
            session, existing.id, secret=password, secret_store=store, actor_id=owner.id
        )
        session.commit()
        return existing
    conn = connection_service.create_connection(
        session,
        name=CONNECTION_NAME,
        conn_type="postgres",
        env="dev",
        config={
            "host": wh["host"],
            "port": wh["port"],
            "database": wh["database"],
            "user": READER,
            "schema": SCHEMA,
            # The bundled warehouse has no TLS; it is reachable only inside the compose network.
            "sslmode": "disable",
        },
        secret=password,
        created_by=owner.id,
        secret_store=store,
    )
    session.commit()
    return conn


def _suite(session: Session, owner: User, connection: Connection, spec: dict[str, Any]) -> Suite:
    suite = session.scalar(
        select(Suite).where(Suite.name == spec["name"], Suite.connection_id == connection.id)
    )
    if suite is None:
        suite = suite_service.create_suite(
            session,
            name=spec["name"],
            description=spec["description"],
            connection_id=connection.id,
            created_by=owner.id,
            target={"schema": SCHEMA, "table": spec["table"]},
        )
        session.commit()
    names = set(session.scalars(select(Check.name).where(Check.suite_id == suite.id)))
    for name, kind, expectation_type, config, thresholds in spec["checks"]:
        if name in names:
            continue
        check_service.create_check(
            session,
            suite_id=suite.id,
            name=name,
            kind=kind,
            expectation_type=expectation_type,
            config=config,
            warn_threshold=thresholds.get("warn_threshold"),
            fail_threshold=thresholds.get("fail_threshold"),
            critical_threshold=thresholds.get("critical_threshold"),
            actor_id=owner.id,
        )
    if (
        spec["schedule"]
        and session.scalar(select(Schedule.id).where(Schedule.suite_id == suite.id)) is None
    ):
        schedule_service.create_schedule(
            session, suite_id=suite.id, cron_expr=spec["schedule"], user_id=owner.id
        )
    session.commit()
    return suite


def _first_run(session: Session, owner: User, suite: Suite) -> bool:
    if session.scalar(select(Run.id).where(Run.suite_id == suite.id)) is not None:
        return False
    run = run_dispatch.new_queued_run(suite, triggered_by=f"manual:{owner.id}")
    session.add(run)
    session.commit()
    return run_dispatch.dispatch_or_fail(session, run, suite_id=str(suite.id))


def seed() -> None:
    wh = _warehouse()
    created, password = prepare_warehouse(wh)
    session = get_session()
    try:
        owner = _upsert_user(
            session,
            aad_object_id=DEV_BYPASS_AAD_OID,
            email=DEV_BYPASS_EMAIL,
            display_name=DEV_BYPASS_DISPLAY_NAME,
        )
        connection = _connection(session, owner, wh, password)
        suites = [_suite(session, owner, connection, spec) for spec in _SUITES]
        _share_with_otp_operators(session, owner=owner, settings=get_settings())
        started = sum(_first_run(session, owner, suite) for suite in suites)
        print(
            f"Seeded demo warehouse: data={'created' if created else 'kept'} "
            f"connection={connection.name} suites={len(suites)} first_runs={started}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    seed()
