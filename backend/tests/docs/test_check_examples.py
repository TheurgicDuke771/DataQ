"""The check-type examples published in the docs are valid checks with honest results."""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from backend.app.datasources.databricks_dqx import DQX_EXPECTATION_TYPES
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    ALLOWLIST_ONLY_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.snowflake_dmf import DMF_EXPECTATION_TYPES
from backend.app.db.models import Connection, Suite, User
from backend.app.services import check_service
from backend.app.services.check_service import COMPARISON_EXPECTATION_TYPES
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE


def _load() -> Any:
    path = Path(__file__).resolve().parents[3] / "scripts" / "docs" / "check_examples.py"
    spec = importlib.util.spec_from_file_location("check_examples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


examples = _load()


@pytest.fixture
def suites(db_session: Any) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    user = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="d@x.io")
    db_session.add(user)
    db_session.flush()
    out = {}
    for conn_type in {e.connection for e in examples.EXAMPLES.values()} | {"s3"}:
        conn = Connection(
            name=f"{conn_type}-{uuid.uuid4().hex[:6]}",
            type=conn_type,
            env="dev",
            config={},
            secret_ref="kv-x",
            created_by=user.id,
        )
        db_session.add(conn)
        db_session.flush()
        suite = Suite(name=conn_type, connection_id=conn.id, created_by=user.id)
        db_session.add(suite)
        db_session.flush()
        out[conn_type] = (suite.id, conn.id)
    db_session.commit()
    return out


def test_every_check_type_has_an_example() -> None:
    known = {
        *ALLOWED_EXPECTATION_TYPES,
        *DMF_EXPECTATION_TYPES,
        *DQX_EXPECTATION_TYPES,
        *COMPARISON_EXPECTATION_TYPES,
        *(f"monitor:{kind}" for kind in ("freshness", "volume", "schema_drift", "anomaly")),
    }
    assert set(examples.EXAMPLES) == known - ALLOWLIST_ONLY_TYPES | {CUSTOM_SQL_EXPECTATION_TYPE}


@pytest.mark.parametrize("expectation_type", sorted(examples.EXAMPLES))
def test_example_is_accepted_by_check_validation(
    db_session: Any, suites: dict[str, tuple[uuid.UUID, uuid.UUID]], expectation_type: str
) -> None:
    example = examples.EXAMPLES[expectation_type]
    frame_only = expectation_type in DATAFRAME_ONLY_EXPECTATION_TYPES
    suite_id, conn_id = suites["s3" if frame_only else example.connection]
    check = check_service.create_check(
        db_session,
        suite_id=suite_id,
        name=expectation_type[:60],
        kind=example.kind,
        expectation_type=expectation_type,
        config=example.config,
        warn_threshold=example.thresholds.get("warn_threshold"),
        fail_threshold=example.thresholds.get("fail_threshold"),
        critical_threshold=example.thresholds.get("critical_threshold"),
        source_connection_id=conn_id if example.kind == "comparison" else None,
        engine=example.engine,
    )
    assert check.expectation_type == expectation_type


def test_recorded_results_match_a_fresh_run() -> None:
    fresh = json.loads(json.dumps(examples.evaluate()))
    assert fresh == json.loads(examples.RESULTS.read_text())
