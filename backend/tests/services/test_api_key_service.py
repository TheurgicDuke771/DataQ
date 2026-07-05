"""api_key_service tests — the PAT credential surface (ADR 0026 phase 1, #461).

Covers the security bar explicitly: hash-at-rest (plaintext never stored),
show-once, uniform 401 for unknown/revoked/expired, cross-user isolation,
last-used throttling, and the owner-lifecycle cascade.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.errors import DataQError
from backend.app.db.models import ApiKey, User
from backend.app.services import api_key_service as svc


@pytest.fixture
def user(db_session: Any) -> User:
    u = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="k@x.io")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def other_user(db_session: Any) -> User:
    u = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="o@x.io")
    db_session.add(u)
    db_session.commit()
    return u


def test_create_returns_prefixed_token_and_stores_only_the_hash(
    db_session: Any, user: User
) -> None:
    key, token = svc.create_key(db_session, user, name="ci")
    assert token.startswith(svc.TOKEN_PREFIX)
    assert key.key_prefix == token[: len(svc.TOKEN_PREFIX) + 4]
    # Hash-at-rest: the plaintext appears nowhere on the row.
    assert token not in (key.key_hash, key.key_prefix, key.name)
    assert len(key.key_hash) == 64  # sha256 hex
    assert key.revoked_at is None and key.last_used_at is None


def test_two_keys_are_distinct(db_session: Any, user: User) -> None:
    _, t1 = svc.create_key(db_session, user, name="a")
    _, t2 = svc.create_key(db_session, user, name="b")
    assert t1 != t2


def test_expiry_bounds_rejected(db_session: Any, user: User) -> None:
    with pytest.raises(DataQError) as e:
        svc.create_key(db_session, user, name="x", expires_in_days=0)
    assert e.value.status_code == 422
    with pytest.raises(DataQError):
        svc.create_key(db_session, user, name="x", expires_in_days=svc.MAX_EXPIRY_DAYS + 1)


def test_resolve_happy_path_stamps_last_used(db_session: Any, user: User) -> None:
    _, token = svc.create_key(db_session, user, name="ci")
    resolved = svc.resolve_token(db_session, token)
    assert resolved.id == user.id
    row = db_session.execute(select(ApiKey)).scalar_one()
    assert row.last_used_at is not None


def test_resolve_throttles_last_used_writes(db_session: Any, user: User) -> None:
    _, token = svc.create_key(db_session, user, name="ci")
    svc.resolve_token(db_session, token)
    first = db_session.execute(select(ApiKey)).scalar_one().last_used_at
    svc.resolve_token(db_session, token)  # immediately again — inside the interval
    assert db_session.execute(select(ApiKey)).scalar_one().last_used_at == first


def test_resolve_unknown_revoked_expired_all_uniform_401(db_session: Any, user: User) -> None:
    # Unknown token.
    with pytest.raises(DataQError) as e:
        svc.resolve_token(db_session, svc.TOKEN_PREFIX + "nope")
    assert e.value.status_code == 401
    unknown_msg = e.value.message

    # Revoked key.
    key, token = svc.create_key(db_session, user, name="r")
    svc.revoke_key(db_session, user, key.id)
    with pytest.raises(DataQError) as e:
        svc.resolve_token(db_session, token)
    assert e.value.status_code == 401
    assert e.value.message == unknown_msg  # no oracle distinguishing the cases

    # Expired key.
    key2, token2 = svc.create_key(db_session, user, name="e")
    key2.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(DataQError) as e:
        svc.resolve_token(db_session, token2)
    assert e.value.status_code == 401
    assert e.value.message == unknown_msg


def test_revoke_is_idempotent_and_owner_scoped(
    db_session: Any, user: User, other_user: User
) -> None:
    key, _ = svc.create_key(db_session, user, name="r")
    first = svc.revoke_key(db_session, user, key.id).revoked_at
    assert first is not None
    assert svc.revoke_key(db_session, user, key.id).revoked_at == first  # idempotent
    # Another user's key: 404, indistinguishable from nonexistent.
    with pytest.raises(DataQError) as e:
        svc.revoke_key(db_session, other_user, key.id)
    assert e.value.status_code == 404


def test_list_is_owner_scoped_newest_first(db_session: Any, user: User, other_user: User) -> None:
    svc.create_key(db_session, user, name="a")
    svc.create_key(db_session, other_user, name="theirs")
    keys = svc.list_keys(db_session, user)
    assert [k.name for k in keys] == ["a"]


def test_user_delete_cascades_keys(db_session: Any, user: User) -> None:
    svc.create_key(db_session, user, name="doomed")
    db_session.delete(user)
    db_session.commit()
    assert db_session.execute(select(ApiKey)).scalar_one_or_none() is None
