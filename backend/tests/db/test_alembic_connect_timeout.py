"""The migrate-job engine needs a bounded initial connect too (#1102 follow-up).

`backend/app/db/session.py` builds the APP engine and got a `connect_timeout` in
#1102, closing the gap where an unreachable DB (network partition, not a locked row)
would block every `get_session()` caller on the OS/driver's connect default (can be
minutes). `backend/alembic/env.py` builds a SEPARATE engine for `alembic upgrade
head` — the same gap was open there too, and it matters more on this path: the
`dataq-app-migrate` job runs before the api/worker roll, so a hung migrate step
blocks the entire deploy rather than one API request.

This can't be a `session.py`-style test that imports the module and captures
`create_engine`'s kwargs: `backend/alembic/env.py` runs `run_migrations_online()` (or
`_offline()`) unconditionally at import time via Alembic's `context`, which is not
configured outside a real `alembic` invocation — importing it here would either raise
or attempt a real migration run. Parsing the source AST for the `connect_args` dict
literal passed to `engine_from_config` is the same "assert something about code we
cannot safely execute in-process" shape as `test_assert_hygiene.py`.

#1266 moved the psycopg2/libpq-only keys (`connect_timeout`, `keepalives*`) out of
this dict literal and behind the shared `psycopg_connect_args` driver guard
(`backend/app/db/pg_connect_args.py`, unit-tested directly in
`test_pg_connect_args.py`) — a non-psycopg driver would otherwise hit `TypeError` on
`engine_from_config`'s first real connect. A follow-up fix moved `options` (the
lock_timeout GUC) in alongside them: it is NOT portable across every driver either
(asyncpg has no `options` connect kwarg at all), so it needed the same guard instead
of staying an unconditional literal dict key. `connect_args={}` is now just the
`**psycopg_connect_args(...)` unpack with no other literal keys, so ALL of the
assertions below walk the AST for that call's keyword arguments rather than for
literal dict keys.
"""

from __future__ import annotations

import ast
import pathlib

_ENV_PY = pathlib.Path(__file__).resolve().parents[3] / "backend" / "alembic" / "env.py"


def _connect_args_dict_node() -> ast.Dict:
    """The `connect_args={...}` dict literal inside `run_migrations_online`'s
    `engine_from_config(...)` call — found by walking the AST rather than grepping
    text, so a reformat (e.g. Black moving the dict onto other lines) can't make this
    test silently stop checking anything."""
    tree = ast.parse(_ENV_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "engine_from_config":
                continue
            for kw in node.keywords:
                if kw.arg == "connect_args" and isinstance(kw.value, ast.Dict):
                    return kw.value
    raise AssertionError(
        "no `engine_from_config(..., connect_args={...})` call found in "
        f"{_ENV_PY} — did the migration engine move or get restructured?"
    )


def _psycopg_connect_args_call_node(connect_args: ast.Dict) -> ast.Call:
    """The `**psycopg_connect_args(...)` unpack entry inside the `connect_args`
    dict literal. A `**expr` entry shows up in `ast.Dict` as a `None` key paired
    positionally with its value node."""
    for key_node, value_node in zip(connect_args.keys, connect_args.values, strict=True):
        if key_node is None and isinstance(value_node, ast.Call):
            func = value_node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "psycopg_connect_args":
                return value_node
    raise AssertionError(
        "no `**psycopg_connect_args(...)` unpack found in the migrate engine's "
        "connect_args — the psycopg2/libpq-only keys (connect_timeout, keepalives*) "
        "need the shared driver guard so a non-psycopg driver degrades instead of "
        "TypeError'ing at the first real connect (#1266)"
    )


def test_the_migration_engine_keeps_the_portable_lock_timeout_option() -> None:
    """`options` (the lock_timeout GUC) must still reach the engine for a psycopg
    driver — but as a KEYWORD ARGUMENT of the `**psycopg_connect_args(...)` unpack,
    not as a literal `connect_args` dict key. A literal `"options": ...` key would be
    unconditional and would raise `TypeError` on a non-psycopg driver's `connect()`
    (asyncpg has no `options` connect kwarg at all) — the same #1266 shape as
    `connect_timeout`/`keepalives*`."""
    connect_args = _connect_args_dict_node()
    literal_keys = [k.value for k in connect_args.keys if isinstance(k, ast.Constant)]
    assert "options" not in literal_keys, (
        "options is a literal connect_args dict key again — it must route through "
        "psycopg_connect_args() like connect_timeout/keepalives* instead, since it "
        "is not actually portable across drivers (asyncpg has no `options` connect "
        "kwarg at all)"
    )

    call = _psycopg_connect_args_call_node(connect_args)
    passed_kwargs = {kw.arg for kw in call.keywords}
    assert "options" in passed_kwargs, "the pre-existing lock_timeout GUC option went missing"


def test_the_migration_engine_routes_psycopg_only_keys_through_the_shared_guard() -> None:
    """#1102 (`connect_timeout`) + #1221 (`keepalives*`), now behind the #1266 guard:
    both keys must still reach the engine — just conditionally, through
    `psycopg_connect_args`, rather than as unconditional dict-literal keys that would
    raise `TypeError` on a non-psycopg driver."""
    connect_args = _connect_args_dict_node()
    call = _psycopg_connect_args_call_node(connect_args)

    passed_kwargs = {kw.arg for kw in call.keywords}
    for key in (
        "connect_timeout",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
    ):
        assert key in passed_kwargs, (
            f"the migrate-job engine's psycopg_connect_args(...) call is missing "
            f"{key} — #1102/#1221's protections would be lost for the psycopg path"
        )


def test_the_migration_engine_imports_the_shared_driver_guard() -> None:
    """Guards against a future edit re-inlining a second, drifted copy of the
    driver-check logic directly in `env.py` instead of reusing
    `backend/app/db/pg_connect_args.py` (the same helper `session.py`'s app engine
    uses) — see #1266."""
    tree = ast.parse(_ENV_PY.read_text())
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "psycopg_connect_args" in imported_names, (
        "env.py no longer imports the shared psycopg_connect_args guard — did the "
        "driver-check logic get re-inlined instead of reused from "
        "backend/app/db/pg_connect_args.py?"
    )
