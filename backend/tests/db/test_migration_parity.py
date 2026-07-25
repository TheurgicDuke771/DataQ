"""The model and the migrations must describe the same schema (#990).

Every test database in this repo is built by ``Base.metadata.create_all``, so the
ORM metadata is the *only* schema tests ever see. `alembic upgrade head` — the
thing production actually runs — is exercised by the deploy job and the E2E lane,
but until now it was never **compared** against the model. Nothing anywhere
failed when the two disagreed.

That gap is not hypothetical. #913 shipped a backfill migration targeting a
column on the wrong table and broke every fresh database; the suite was green
throughout, because the suite never applied a migration. The same shape covers a
missing migration for a new column, a CHECK constraint that exists only in the
model, an `ondelete` that differs, and the partial-index predicate #457 guards in
Python but could not guard in SQL (see `test_orchestration_predicate_drift.py`).

There are **two** checks here, because one is not enough:

* `test_the_migrations_build_the_schema_the_model_describes` — Alembic's own
  autogenerate comparison against a scratch database built by the real migration
  chain. A non-empty diff means "autogenerate would want to write something".
* `test_vocabularies_in_predicates_match_between_model_and_database` — the
  partial-index predicates and CHECK constraint bodies that `compare_metadata`
  **does not look at**. See the comment above it; without this second check the
  module would claim to close #990 while missing the case #990 names as its
  minimum bar.

**This test is slow by nature** (it builds a database and replays every
migration), so it is marked and runs once per session.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy.engine import make_url

from backend.app.db import models  # noqa: F401  (register models on Base.metadata)
from backend.app.db.base import Base
from backend.tests.conftest import TEST_DATABASE_URL

# A database of its own, not a schema inside the test DB: migrations address
# unqualified names and `create_all` already owns the test database's `public`.
_SCRATCH_DB = "dataq_migration_parity"

_REPO_ROOT = Path(__file__).resolve().parents[3]


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="no TEST_DATABASE_URL — see conftest's resolution order"
)


def _admin_engine() -> Any:
    """A connection to `postgres` on the same server, for CREATE/DROP DATABASE."""
    return create_engine(
        make_url(str(TEST_DATABASE_URL)).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )


@pytest.fixture(scope="session")
def migrated_url() -> Iterator[str]:
    """A scratch database built by `alembic upgrade head`, dropped afterwards."""
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{_SCRATCH_DB}"'))
    except Exception as exc:  # pragma: no cover — depends on the server's grants
        admin.dispose()
        pytest.skip(f"cannot create a scratch database for the parity check: {type(exc).__name__}")
    admin.dispose()

    target = make_url(str(TEST_DATABASE_URL)).set(database=_SCRATCH_DB)
    url = target.render_as_string(hide_password=False)
    # A subprocess, not an in-process alembic call: `alembic/env.py` reads the URL
    # from `get_settings()`, which is cached per process — driving it through the
    # environment is both simpler and closer to what the deploy job runs.
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT / "backend",
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(
            "`alembic upgrade head` failed on a fresh database — the migration chain "
            f"is broken:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        )

    try:
        yield url
    finally:
        admin = _admin_engine()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}" WITH (FORCE)'))
        admin.dispose()


def test_the_migrations_build_the_schema_the_model_describes(migrated_url: str) -> None:
    """`alembic upgrade head` and `Base.metadata` must agree.

    A failure here names the divergence directly: `add_column` means the model has
    something no migration creates (ship the migration), `remove_column` means a
    migration creates something the model dropped without a follow-up, and
    `modify_*` means a type/default/nullability differs.

    Note what this does NOT prove: that the migration is *safe* to deploy
    (backward compatibility is a review concern, CONTRIBUTING rule 30), only that
    the two descriptions match.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_engine(migrated_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # `alembic_version` is alembic's own bookkeeping and is deliberately absent
    # from the model — it is the one expected difference, not drift.
    drift = [d for d in diffs if "alembic_version" not in repr(d)]

    assert not drift, "model and migrations disagree:\n" + "\n".join(repr(d) for d in drift)


# ── the drift `compare_metadata` cannot see ──────────────────────────────────
#
# Alembic's autogenerate comparison is thorough about columns, types and foreign
# keys, but it does NOT inspect two things this codebase leans on heavily:
#
#   * a partial index's WHERE predicate — `alembic/ddl/postgresql.py` compares
#     only `postgresql_nulls_not_distinct` among an index's dialect options, so
#     the predicate is never looked at;
#   * a CHECK constraint's body, or its absence from the database entirely.
#
# Both are exactly where this repo encodes its closed vocabularies
# (`ORCHESTRATION_PROVIDERS`, `CONNECTION_TYPES`, `CHECK_KINDS`, the status
# tuples). #457's whole subject — a provider widened in the model and the service
# but never in a migration — lives in the first bullet, so a parity test that
# stopped at `compare_metadata` would claim to cover #990's stated minimum while
# not covering it. Verified: that scenario produces zero autogenerate diffs.
#
# Comparing the SQL text directly is hopeless (Postgres rewrites
# `type IN ('adf','airflow')` into an `= ANY (ARRAY[...])` form), so this compares
# what actually matters and survives normalisation: the set of **string literals**
# in each predicate. A vocabulary value present on one side and not the other is
# precisely the drift, and quoting is preserved verbatim by both renderings.

_LITERAL = re.compile(r"'([^']*)'")


def _literals(sql: str) -> set[str]:
    return set(_LITERAL.findall(sql))


def _model_predicates() -> dict[str, str]:
    """`{object name: SQL text}` for every named CHECK and partial index in the model."""
    out: dict[str, str] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name:
                out[str(constraint.name)] = str(constraint.sqltext)
        for index in table.indexes:
            # `dialect_kwargs` is the flat accessor — `dialect_options` is a
            # special mapping that does not type as a plain dict.
            where = index.dialect_kwargs.get("postgresql_where")
            if where is not None and index.name:
                out[str(index.name)] = str(where)
    return out


def _live_predicates(conn: Any) -> dict[str, str]:
    """`{object name: SQL text}` for the same objects, as the database has them."""
    live: dict[str, str] = {}
    for name, definition in conn.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
        )
    ):
        live[name] = definition
    for name, definition in conn.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
    ):
        _, sep, predicate = definition.partition(" WHERE ")
        if sep:
            live[name] = predicate
    return live


def test_vocabularies_in_predicates_match_between_model_and_database(migrated_url: str) -> None:
    """Every value a CHECK or partial-index predicate lists must match on both sides.

    This is the leg `compare_metadata` is blind to, and it is #990's stated
    minimum bar. The failure it exists to catch: add a fourth orchestration
    provider, widen `ORCHESTRATION_PROVIDERS`, the model's `postgresql_where` and
    the service constant — all correctly — and ship no migration. Every other
    test stays green while production's dedup index silently stops covering the
    new provider, so a re-delivered webhook creates a duplicate run.

    An object missing from the database entirely is reported too: a CHECK declared
    only in the model is the same defect wearing a different hat.
    """
    engine = create_engine(migrated_url)
    try:
        with engine.connect() as conn:
            live = _live_predicates(conn)
    finally:
        engine.dispose()

    drift: list[str] = []
    for name, model_sql in _model_predicates().items():
        if name not in live:
            drift.append(f"{name}: declared in the model, absent from the migrated database")
            continue
        expected, actual = _literals(model_sql), _literals(live[name])
        if expected != actual:
            drift.append(
                f"{name}: model has {sorted(expected)}, database has {sorted(actual)}"
                f" (missing from the database: {sorted(expected - actual)};"
                f" extra: {sorted(actual - expected)})"
            )

    assert not drift, "predicate drift between the model and the migrations:\n" + "\n".join(drift)
