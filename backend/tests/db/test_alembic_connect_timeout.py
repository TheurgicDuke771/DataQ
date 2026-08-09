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


def test_the_migration_engine_bounds_the_initial_connect_too() -> None:
    connect_args = _connect_args_dict_node()
    keys = [k.value for k in connect_args.keys if isinstance(k, ast.Constant)]

    assert "options" in keys, "the pre-existing lock_timeout GUC option went missing"
    assert "connect_timeout" in keys, (
        "the migrate-job engine has no connect_timeout — an unreachable DB at deploy "
        "time would hang `alembic upgrade head` (and the whole deploy) on the "
        "OS/driver connect default instead of failing fast (#1102)"
    )


def test_the_migration_engine_bounds_a_warm_connection_too() -> None:
    """#1221 mirrored onto the migrate-job engine (review finding on the #1221 PR): the
    app engine's `connect_timeout` gap (#1102, above) has a sibling — it bounds only the
    initial connect, not a connection that's already open when a network partition
    happens silently later. `NullPool` means this engine never reuses a connection
    across migrations, but it still holds ONE connection open for an entire migration's
    duration, long enough for a mid-migration partition to hang a read forever and block
    the whole deploy. TCP keepalives make the OS detect and reap a dead socket instead.

    Same AST approach as the test above, for the same reason: `env.py` runs migrations
    unconditionally at import time via Alembic's `context`, so it can't be imported and
    exercised directly in a unit test.
    """
    connect_args = _connect_args_dict_node()
    keys = [k.value for k in connect_args.keys if isinstance(k, ast.Constant)]

    for key in ("keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count"):
        assert key in keys, (
            f"the migrate-job engine has no {key} — a network partition mid-migration "
            "would hang the whole deploy with no way to detect the dead connection (#1221)"
        )
