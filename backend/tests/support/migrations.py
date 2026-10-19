"""The harness an up/down migration test runs one revision file through, against the test
session's connection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.app.db.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def load_migration(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_migration_{filename[:12]}", VERSIONS / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_migration(db: Any, filename: str, direction: str) -> None:
    """Run ``upgrade`` or ``downgrade`` of one revision file on the session's connection."""
    module = load_migration(filename)
    # The env's target metadata, so the naming convention prefixes constraint names exactly as
    # `alembic upgrade` does in production.
    ctx = MigrationContext.configure(db.connection(), opts={"target_metadata": Base.metadata})
    with Operations.context(ctx):
        getattr(module, direction)()
    db.expire_all()


def table_exists(db: Any, table: str) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :table"
            ),
            {"table": table},
        )
    )
