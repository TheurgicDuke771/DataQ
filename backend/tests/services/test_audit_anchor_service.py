"""Anchor read-back against the local chain — #2310."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.config import get_settings
from backend.app.core.tamper_anchor import (
    NoopTamperAnchor,
    ObjectLockAnchor,
    StoredObject,
    WebhookTamperAnchor,
)
from backend.app.db.models import AuditChainCheckpoint
from backend.app.services import audit_anchor_service, audit_chain, audit_service


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail: Exception | None = None

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None:
        self.objects[key] = body

    def list_objects(self, prefix: str) -> list[StoredObject]:
        if self.fail is not None:
            raise self.fail
        return [StoredObject(k) for k in self.objects if k.startswith(prefix)]

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        return [self.objects[o.key] for o in objects]


def _anchor(store: _Store) -> ObjectLockAnchor:
    return ObjectLockAnchor(store, prefix="audit-anchors", retention_days=30)


def _write(
    store: _Store, head_hash: str, *, as_of: datetime, label: str = "audit_chain_daily"
) -> str:
    key = f"audit-anchors/{as_of:%Y%m%dT%H%M%S%f}Z-{head_hash}.json"
    store.objects[key] = json.dumps(
        {"label": label, "head_hash": head_hash, "event_count": 1, "as_of": as_of.isoformat()}
    ).encode()
    return key


def _commit_event(session: Any) -> str:
    event = audit_service.record(
        session,
        action="check.update",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor=None,
        actor_kind="webhook",
    )
    session.commit()
    assert event.row_hash is not None
    return event.row_hash


def test_an_unreadable_anchor_mode_yields_none(db_session: Any) -> None:
    assert audit_anchor_service.verify_anchors(db_session, NoopTamperAnchor()) is None
    assert (
        audit_anchor_service.verify_anchors(db_session, WebhookTamperAnchor("http://x", "s"))
        is None
    )


def test_no_anchors_published_is_empty(db_session: Any) -> None:
    result = audit_anchor_service.verify_anchors(db_session, _anchor(_Store()))
    assert result is not None
    assert result.status == "empty"
    assert result.checked_count == 0


def test_a_published_head_that_is_in_the_chain_is_present(db_session: Any) -> None:
    store = _Store()
    head = _commit_event(db_session)
    _write(store, head, as_of=datetime.now(UTC))

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "ok"
    assert (result.checked_count, result.present_count, result.missing_count) == (1, 1, 0)
    assert result.findings == []


def test_a_published_head_the_chain_never_reaches_is_a_mismatch(db_session: Any) -> None:
    """The restore-detection story: the anchor remembers a head the restored
    database has never seen."""
    store = _Store()
    _commit_event(db_session)
    now = datetime.now(UTC)
    key = _write(store, "0" * 64, as_of=now)

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "mismatch"
    assert result.missing_count == 1
    [finding] = result.findings
    assert finding.key == key
    assert finding.match == "missing"
    assert finding.head_hash == "0" * 64
    assert finding.label == "audit_chain_daily"


def test_a_head_older_than_the_last_purge_is_explained_not_missing(db_session: Any) -> None:
    store = _Store()
    _commit_event(db_session)
    cutoff = datetime.now(UTC) - timedelta(days=400)  # a real sweep: older than retention
    db_session.add(AuditChainCheckpoint(cutoff=cutoff, deleted_count=5))
    db_session.commit()
    _write(store, "1" * 64, as_of=cutoff - timedelta(days=1))  # purged by that sweep
    newer = _write(store, "2" * 64, as_of=cutoff + timedelta(days=1))  # should still exist

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "mismatch"
    assert (result.purged_count, result.missing_count) == (1, 1)
    assert [f.key for f in result.findings] == [newer]


def test_a_forged_future_checkpoint_cannot_explain_missing_anchors(db_session: Any) -> None:
    """One inserted checkpoint row with a far-future cutoff would otherwise turn every
    missing anchor into "purged" — the exact cover a chain rewrite needs."""
    store = _Store()
    _commit_event(db_session)
    db_session.add(AuditChainCheckpoint(cutoff=datetime(2999, 1, 1, tzinfo=UTC), deleted_count=0))
    db_session.add(
        AuditChainCheckpoint(cutoff=datetime.now(UTC) - timedelta(days=1), deleted_count=0)
    )
    db_session.commit()
    key = _write(store, "4" * 64, as_of=datetime.now(UTC) - timedelta(days=30))

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "mismatch"
    assert (result.purged_count, result.missing_count) == (0, 1)
    assert [f.key for f in result.findings] == [key]


def test_no_retention_means_nothing_is_explained_by_a_purge(
    db_session: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
    get_settings.cache_clear()
    store = _Store()
    db_session.add(
        AuditChainCheckpoint(cutoff=datetime.now(UTC) - timedelta(days=400), deleted_count=1)
    )
    db_session.commit()
    _write(store, "5" * 64, as_of=datetime.now(UTC) - timedelta(days=500))

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))
    get_settings.cache_clear()

    assert result is not None
    assert result.missing_count == 1


def test_a_purge_anchors_last_deleted_hash_counts_as_present(db_session: Any) -> None:
    """`audit_events_purge` publishes the hash of a row that is deleted on purpose;
    the checkpoint keeps it, so it is present, not purged-and-forgotten."""
    store = _Store()
    cutoff = datetime.now(UTC) - timedelta(days=10)
    db_session.add(
        AuditChainCheckpoint(cutoff=cutoff, deleted_count=5, last_deleted_row_hash="3" * 64)
    )
    db_session.commit()
    _write(store, "3" * 64, as_of=cutoff, label="audit_events_purge")

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "ok"
    assert result.present_count == 1


def test_a_foreign_object_under_the_prefix_is_reported_as_malformed(db_session: Any) -> None:
    store = _Store()
    store.objects["audit-anchors/19990101T000000000000Z-junk.json"] = b"not json"

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "mismatch"
    assert result.malformed_count == 1
    assert result.findings[0].match == "malformed"


def test_a_store_outage_is_unavailable_never_empty(db_session: Any) -> None:
    store = _Store()
    store.fail = ConnectionError("endpoint down")

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.status == "unavailable"
    assert result.error is not None and "endpoint down" in result.error
    assert result.checked_count == 0


def test_findings_are_capped_but_counts_are_complete(db_session: Any) -> None:
    store = _Store()
    base = datetime.now(UTC)
    for n in range(audit_anchor_service.MAX_REPORTED + 5):
        _write(store, f"{n:064x}", as_of=base + timedelta(seconds=n))

    result = audit_anchor_service.verify_anchors(db_session, _anchor(store))

    assert result is not None
    assert result.missing_count == audit_anchor_service.MAX_REPORTED + 5
    assert len(result.findings) == audit_anchor_service.MAX_REPORTED
    # Newest first — the most recent restore evidence leads.
    assert result.findings[0].head_hash == f"{audit_anchor_service.MAX_REPORTED + 4:064x}"


def test_the_default_anchor_is_the_configured_one(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.core import tamper_anchor

    monkeypatch.setattr(tamper_anchor, "_anchor_singleton", NoopTamperAnchor())
    assert audit_anchor_service.verify_anchors(db_session) is None
    assert audit_chain.verify_chain(db_session).first_break is None
