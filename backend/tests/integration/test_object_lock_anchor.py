"""Opt-in live lane: the object-lock tamper anchor against a real S3-compatible store
with Object Lock (MinIO locally; AWS S3 with the same variables) — #2310.

    docker run -d --name lock-minio -p 127.0.0.1:9100:9000 \\
        -e MINIO_ROOT_USER=... -e MINIO_ROOT_PASSWORD=... minio/minio server /data
    OBJECT_LOCK_TEST_ENDPOINT=http://127.0.0.1:9100 OBJECT_LOCK_TEST_ACCESS_KEY_ID=... \\
        OBJECT_LOCK_TEST_SECRET_ACCESS_KEY=... \\
        pytest backend/tests/integration/test_object_lock_anchor.py

The bucket is created with Object Lock enabled (`--with-lock` in mc terms) if it does
not exist. Retention is one day, so a locked bucket outlives the run — use a throwaway
server. Real AWS Object Lock and Azure immutability are not covered here (#2224).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.app.core.config import Settings
from backend.app.core.tamper_anchor import ObjectLockAnchor, build_object_lock_anchor
from backend.app.services import audit_anchor_service, audit_service

_ENV = "OBJECT_LOCK_TEST_"
ENDPOINT = os.environ.get(f"{_ENV}ENDPOINT", "").strip()
pytestmark = pytest.mark.skipif(not ENDPOINT, reason=f"requires {_ENV}ENDPOINT (opt-in live lane)")

BUCKET = os.environ.get(f"{_ENV}BUCKET", "dataq-anchor-lane")
REGION = os.environ.get(f"{_ENV}REGION", "us-east-1")
MODE = os.environ.get(f"{_ENV}MODE", "COMPLIANCE")


@pytest.fixture(scope="module")
def raw_client() -> Any:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.environ[f"{_ENV}ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ[f"{_ENV}SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise
    assert client.get_object_lock_configuration(Bucket=BUCKET)["ObjectLockConfiguration"] == {
        "ObjectLockEnabled": "Enabled"
    }
    return client


@pytest.fixture
def anchor(monkeypatch: pytest.MonkeyPatch) -> ObjectLockAnchor:
    """The anchor exactly as `get_tamper_anchor` would build it, with the secret
    going through the (env) SecretStore like any other."""
    monkeypatch.setenv("SECRET_STORE", "env")
    monkeypatch.setenv("KV_SECRET_ANCHOR_LANE_KEY", os.environ[f"{_ENV}SECRET_ACCESS_KEY"])
    settings = Settings(
        _env_file=None,
        secret_store="env",
        tamper_anchor="object_lock",
        tamper_anchor_object_lock_bucket=BUCKET,
        tamper_anchor_object_lock_prefix=f"lane/{uuid.uuid4().hex[:8]}",
        tamper_anchor_object_lock_endpoint_url=ENDPOINT,
        tamper_anchor_object_lock_region=REGION,
        tamper_anchor_object_lock_access_key_id=os.environ[f"{_ENV}ACCESS_KEY_ID"],
        tamper_anchor_object_lock_secret_name="anchor-lane-key",
        tamper_anchor_object_lock_retention_days=1,
        tamper_anchor_object_lock_mode=MODE,
    )
    from backend.app.core import secrets

    secrets.reset_secret_store_cache()
    return build_object_lock_anchor(settings)


def _single_version(raw_client: Any, prefix: str) -> dict[str, Any]:
    versions = raw_client.list_object_versions(Bucket=BUCKET, Prefix=prefix + "/").get(
        "Versions", []
    )
    assert len(versions) == 1, versions
    return dict(versions[0])


def test_the_anchor_is_written_with_retention_and_cannot_be_deleted(
    anchor: ObjectLockAnchor, raw_client: Any
) -> None:
    from botocore.exceptions import ClientError

    head = uuid.uuid4().hex * 2
    assert anchor.publish(
        label="audit_chain_daily", head_hash=head, event_count=1, as_of=datetime.now(UTC)
    )

    version = _single_version(raw_client, anchor.prefix)
    retention = raw_client.get_object_retention(
        Bucket=BUCKET, Key=version["Key"], VersionId=version["VersionId"]
    )["Retention"]
    assert retention["Mode"] == MODE
    assert retention["RetainUntilDate"] > datetime.now(UTC)

    with pytest.raises(ClientError) as refused:
        raw_client.delete_object(Bucket=BUCKET, Key=version["Key"], VersionId=version["VersionId"])
    assert refused.value.response["Error"]["Code"] in {"AccessDenied", "InvalidRequest"}

    read = anchor.read_anchors()
    assert [r.head_hash for r in read.records] == [head]
    assert read.records[0].retain_until is not None


def test_a_delete_marker_does_not_hide_the_anchor_from_verification(
    anchor: ObjectLockAnchor, raw_client: Any
) -> None:
    head = uuid.uuid4().hex * 2
    assert anchor.publish(
        label="audit_chain_daily", head_hash=head, event_count=1, as_of=datetime.now(UTC)
    )
    key = _single_version(raw_client, anchor.prefix)["Key"]

    # A versionless delete on a versioned bucket succeeds — it only adds a delete marker.
    raw_client.delete_object(Bucket=BUCKET, Key=key)
    assert not raw_client.list_objects_v2(Bucket=BUCKET, Prefix=anchor.prefix + "/").get("Contents")

    assert [r.head_hash for r in anchor.read_anchors().records] == [head]


def test_verify_matches_a_real_head_and_flags_a_foreign_one(
    anchor: ObjectLockAnchor, db_session: Any
) -> None:
    event = audit_service.record(
        db_session,
        action="check.update",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor=None,
        actor_kind="webhook",
    )
    db_session.commit()
    assert event.row_hash is not None
    now = datetime.now(UTC)
    assert anchor.publish(
        label="audit_chain_daily", head_hash=event.row_hash, event_count=1, as_of=now
    )
    assert anchor.publish(label="audit_chain_daily", head_hash="0" * 64, event_count=1, as_of=now)

    result = audit_anchor_service.verify_anchors(db_session, anchor)

    assert result is not None
    assert result.status == "mismatch"
    assert (result.checked_count, result.present_count, result.missing_count) == (2, 1, 1)
    assert result.findings[0].head_hash == "0" * 64
