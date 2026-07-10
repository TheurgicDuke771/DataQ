"""`refresh_dbt_lineage` worker task tests (ADR 0034, #759).

The dbt-manifest lineage refresh moved OFF the orchestration ingest path into this
Celery task (own session, own single secret fetch) so the webhook ACK / poll loop
never blocks on artifact IO. These exercise the task body (`_refresh_dbt_lineage`)
end-to-end against a real Postgres with a monkeypatched provider, plus the fail-open
posture for every step (no connection / no capability / no secret / no manifest /
parse blow-up). Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.app.core.secrets import SecretNotFoundError
from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage import dbt_manifest
from backend.app.worker import tasks

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NS = "snowflake://acct"
_ORDERS_HEADER = "DATAQ_DB.RETAIL.ORDERS_HEADER"


class _FakeStore:
    def __init__(self, **data: str) -> None:
        self.data = dict(data)

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:  # pragma: no cover - unused
        self.data[name] = value

    def delete(self, name: str) -> None:  # pragma: no cover - unused
        self.data.pop(name, None)


class _FakeDbtProvider:
    """dbt-shaped provider returning canned manifest bytes (no store IO)."""

    provider = "dbt"
    resource_config_key = "project_name"

    def __init__(self, manifest: bytes | None) -> None:
        self._manifest = manifest

    def read_manifest(self, config: Any, secret: str, job: str) -> bytes | None:
        return self._manifest


def _dbt_connection(
    db_session: Any, *, env: str = "dev", secret_ref: str | None = "kv-x"
) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"dbt-{uuid.uuid4().hex[:8]}",
        type="dbt",
        env=env,
        config={"project_name": "dataq_lineage", "artifacts_uri": "file:///x", "jobs": ["j"]},
        secret_ref=secret_ref,
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _anchor(db_session: Any, *, name: str = _ORDERS_HEADER, env: str = "dev") -> None:
    db_session.add(Asset(namespace=_NS, name=name, env=env))
    db_session.commit()


def _use_provider(monkeypatch: Any, provider: Any) -> None:
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _type: provider)


def _run(db_session: Any, conn: Connection, *, store: _FakeStore | None = None) -> str:
    return tasks._refresh_dbt_lineage(
        db_session,
        connection_id=conn.id,
        job="j",
        secret_store=store or _FakeStore(**{"kv-x": "sas"}),
    )


# ── happy path: fetch → parse → refresh writes edges ──────────────────────────


def test_task_refreshes_lineage_end_to_end(db_session: Any, monkeypatch: Any) -> None:
    conn = _dbt_connection(db_session)
    _anchor(db_session)  # seed the namespace so anchoring resolves
    _use_provider(monkeypatch, _FakeDbtProvider((_FIXTURES / "dbt_manifest_v1.json").read_bytes()))

    assert _run(db_session, conn) == "refreshed"
    edges = db_session.scalars(
        select(LineageEdge).where(LineageEdge.connection_id == conn.id)
    ).all()
    assert len(edges) == 8  # the known harness graph


# ── fail-open per step (never raises; one dbt_lineage_refresh_* family) ────────


def test_task_no_connection(db_session: Any) -> None:
    assert (
        tasks._refresh_dbt_lineage(
            db_session, connection_id=uuid.uuid4(), job="j", secret_store=_FakeStore()
        )
        == "no_connection"
    )


def test_task_no_capability(db_session: Any, monkeypatch: Any) -> None:
    conn = _dbt_connection(db_session)

    class _NoManifest:
        provider = "dbt"
        resource_config_key = "project_name"

    _use_provider(monkeypatch, _NoManifest())
    assert _run(db_session, conn) == "no_capability"


def test_task_no_secret(db_session: Any, monkeypatch: Any) -> None:
    conn = _dbt_connection(db_session, secret_ref=None)
    _use_provider(monkeypatch, _FakeDbtProvider(b"{}"))
    assert _run(db_session, conn) == "no_secret"


def test_task_no_manifest_published(db_session: Any, monkeypatch: Any) -> None:
    conn = _dbt_connection(db_session)
    _use_provider(monkeypatch, _FakeDbtProvider(None))  # not yet published
    assert _run(db_session, conn) == "no_manifest"


def test_task_fails_open_on_parse_error(db_session: Any, monkeypatch: Any) -> None:
    conn = _dbt_connection(db_session)
    _anchor(db_session)
    _use_provider(monkeypatch, _FakeDbtProvider(b'{"garbage": true}'))

    def _boom(_raw: bytes) -> None:
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(dbt_manifest, "parse_manifest", _boom)
    # Never raises — swallowed to "error", and nothing written.
    assert _run(db_session, conn) == "error"
    assert db_session.scalars(select(LineageEdge)).all() == []
