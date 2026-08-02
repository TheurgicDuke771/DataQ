"""/me endpoint tests — focus on the `is_workspace_admin` flag the Admin nav
gates on. Auth is dev-bypass (conftest); WORKSPACE_ADMIN_EMAILS toggles whether
the caller is an admin. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.core.config import get_settings
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import api_key_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_me_flags_non_admin_by_default(client: TestClient) -> None:
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["email"] == DEV_BYPASS_EMAIL
    assert body["is_workspace_admin"] is False


def test_me_flags_admin_when_allowlisted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", f"someone@else.io,{DEV_BYPASS_EMAIL}")
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["is_workspace_admin"] is True


def test_me_serialises_a_null_aad_object_id(client: TestClient, db_session: Any) -> None:
    """`/me` must return 200 with `aad_object_id: null` for a non-AAD identity.

    `MeResponse.aad_object_id` was non-optional and the handler uses
    `model_validate` under a `response_model`, so the moment #734 provisions an
    email-OTP user (NULL aad — ADR 0032 decision 6) every `/me` call by that user
    would have been a response-validation 500. The PAT branch of the seam is what
    lets a test reach `/me` as a user other than the dev-bypass identity.
    """
    owner = User(
        id=uuid.uuid4(), aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@example.com"
    )
    db_session.add(owner)
    db_session.commit()
    _, token = api_key_service.create_key(db_session, owner, name="otp-me")

    resp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == owner.email
    assert body["aad_object_id"] is None
