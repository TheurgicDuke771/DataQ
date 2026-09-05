"""Do the admin suite writes actually COMMIT their audit events? (#1698)

The shared-session fixture reads through the same transaction the request wrote in,
so a write that never commits still reads back — this verifies on a second connection.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import AuditEvent, Base, Connection, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.conftest import TEST_DATABASE_URL

_DB = "dataq_admin_suite_write_probe"


@pytest.fixture
def probe_engine() -> Iterator[Any]:
    if not TEST_DATABASE_URL:
        pytest.skip("needs TEST_DATABASE_URL")
    admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            try:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
                conn.execute(text(f'CREATE DATABASE "{_DB}"'))
            except ProgrammingError as exc:  # pragma: no cover - permission-dependent
                pytest.skip(f"cannot create a probe database: {exc}")
    finally:
        admin.dispose()

    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{_DB}"
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
        admin.dispose()


def test_a_transfer_commits_its_audit_event(probe_engine: Any) -> None:
    """Transfer on one connection; verify the row on another."""
    make_session = sessionmaker(bind=probe_engine)

    setup: Session = make_session()
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    new_owner = User(aad_object_id=uuid.uuid4().hex, email=f"n-{uuid.uuid4().hex[:8]}@example.com")
    setup.add_all([owner, new_owner])
    setup.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    setup.add(conn)
    setup.flush()
    suite = Suite(name="transfer-me", connection_id=conn.id, created_by=owner.id)
    setup.add(suite)
    setup.commit()
    suite_id, owner_id, new_owner_id = suite.id, owner.id, new_owner.id
    setup.close()

    request_session: Session = make_session()
    app.dependency_overrides[get_db] = lambda: request_session
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/suites/{suite_id}/transfer",
            json={"new_owner_user_id": str(new_owner_id)},
        )
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
        request_session.close()

    verify: Session = make_session()
    try:
        event = verify.scalars(
            select(AuditEvent).where(AuditEvent.action == "suite.transfer")
        ).one()
        assert event.after is not None
        assert event.after["owner_id"] == str(new_owner_id)
        # The state change itself committed too, not just its record.
        assert verify.get(Suite, suite_id).created_by == new_owner_id  # type: ignore[union-attr]
        kept = verify.scalars(
            select(Share).where(Share.suite_id == suite_id, Share.user_id == owner_id)
        ).one()
        assert kept.permission == "edit"
    finally:
        verify.close()
