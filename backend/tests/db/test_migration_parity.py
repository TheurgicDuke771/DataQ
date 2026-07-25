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

The check itself is Alembic's own autogenerate comparison, pointed at a scratch
database built by the real migration chain: a non-empty diff means "if you ran
`alembic revision --autogenerate` right now, it would want to write something" —
i.e. the model and the migrations disagree.

**This test is slow by nature** (it builds a database and replays every
migration), so it is marked and runs once per session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
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
