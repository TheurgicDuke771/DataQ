"""The model and the migrations must describe the same schema (#990)."""

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

from backend.app.db import models
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
    # A subprocess, not an in-process alembic call: `alembic/env.py` reads the URL from
    # `get_settings()`, which is cached per process.
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
    """`alembic upgrade head` and `Base.metadata` must agree."""
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


# ── the drift `compare_metadata` cannot see ────────────────────────────────── Alembic's
# autogenerate comparison is thorough about columns, types and foreign keys.

_LITERAL = re.compile(r"'([^']*)'")


def _literals(sql: str) -> set[str]:
    return set(_LITERAL.findall(sql))


# `<column> IS [NOT] NULL` carries no string literal, so `_literals()` can't see a polarity flip
# (#1326). Postgres reformats predicates on read-back — `col IS NULL` -> `(col IS NULL)` — so
# tolerate surrounding parens; a `::type` cast is defensive (no live predicate hits it today), and
# a multi-word type name (e.g. `character varying`) must not swallow the column's own name. `NOT
# (col IS NULL)` is a human-authored inversion the reformatted side never produces, so an outer NOT
# is also unwrapped rather than silently flipping the polarity read off it.
_NULL_CLAUSE = re.compile(
    r"(?:\bNOT\b\s+)?\(*\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)*"
    r"(?:::\"?[a-zA-Z_][a-zA-Z_ ]*?\"?)?\s*\)*\s+IS\s+(NOT\s+)?NULL",
    re.IGNORECASE,
)


def _null_polarities(sql: str) -> set[tuple[str, bool]]:
    """`{(column, is_not_null)}` for every `IS [NOT] NULL` clause in `sql`.

    No quote-awareness: a string literal containing the text "x IS NULL" would false-positive,
    same accepted limitation as `_literals()` itself — no current predicate has one.
    """
    out: set[tuple[str, bool]] = set()
    for match in _NULL_CLAUSE.finditer(sql):
        not_wrapped = bool(re.match(r"\s*NOT\b", match.group(0), re.IGNORECASE))
        out.add((match.group(1).lower(), bool(match.group(2)) != not_wrapped))
    return out


def _model_predicates() -> dict[str, str]:
    """`{object name: SQL text}` for every named CHECK and partial index in the model."""
    # `models` is imported for its side effect — defining the classes is what puts their tables on
    # `Base.metadata`.
    assert (
        models.Connection.__tablename__ in Base.metadata.tables
    ), "no model tables are registered — the comparison would have nothing to do"
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
    """Every literal and every IS [NOT] NULL clause a CHECK or partial-index predicate carries must
    match on both sides — literal-set alone is blind to a bare polarity flip (#1326).
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
        expected_nulls, actual_nulls = _null_polarities(model_sql), _null_polarities(live[name])
        if expected_nulls != actual_nulls:
            drift.append(
                f"{name}: model has IS [NOT] NULL clauses {sorted(expected_nulls)}, database has "
                f"{sorted(actual_nulls)}"
            )

    assert not drift, "predicate drift between the model and the migrations:\n" + "\n".join(drift)


def test_null_polarities_normalizes_model_and_database_predicate_forms() -> None:
    """Postgres reformats `col IS NULL` as `(col IS NULL)` — verified against a live
    `ix_results_unpurged_created` (#1326); the extractor must treat both forms as identical.
    """
    model_sql = (
        "sample_failures_purged_at IS NULL AND sample_failures IS NOT NULL "
        "AND jsonb_typeof(sample_failures) <> 'null'"
    )
    live_sql = (
        "(sample_failures_purged_at IS NULL) AND (sample_failures IS NOT NULL) "
        "AND (jsonb_typeof(sample_failures) <> 'null'::text)"
    )
    expected = {("sample_failures_purged_at", False), ("sample_failures", True)}
    assert _null_polarities(model_sql) == _null_polarities(live_sql) == expected


def test_null_polarities_catches_a_polarity_flip() -> None:
    """A bare `IS NULL` -> `IS NOT NULL` inversion on one side must break equality (#1326) — the
    exact regression `_literals()` cannot see, since neither side gains a string literal.
    """
    model_sql = "sample_failures_purged_at IS NULL"
    flipped_live_sql = "(sample_failures_purged_at IS NOT NULL)"
    assert _null_polarities(model_sql) == {("sample_failures_purged_at", False)}
    assert _null_polarities(flipped_live_sql) == {("sample_failures_purged_at", True)}
    assert _null_polarities(model_sql) != _null_polarities(flipped_live_sql)


def test_null_polarities_tolerates_a_multi_word_cast() -> None:
    """A single-word-only cast pattern re-anchors on the cast's trailing word, losing the real
    column and inventing a fake one from part of the type name — verified failing on the
    pre-fix regex: `(col)::character varying IS NULL` extracted `{('varying', False)}`.
    """
    assert _null_polarities("(col)::character varying IS NULL") == {("col", False)}


def test_null_polarities_unwraps_an_outer_not() -> None:
    """A human-authored `NOT (col IS NULL)` means `col IS NOT NULL` — read literally off the
    inner clause alone it is the opposite polarity, silently. Postgres itself never emits this
    form (it canonicalizes to `IS NOT NULL` directly), so only a hand-written model predicate can
    hit it, but the extractor must not misread one that does.
    """
    assert _null_polarities("NOT (col IS NULL)") == {("col", True)}
    assert _null_polarities("NOT (col IS NOT NULL)") == {("col", False)}
