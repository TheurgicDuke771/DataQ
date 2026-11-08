"""The audited payload scrub — ADR 0041 §9, 2026-11-08 amendment (#2547)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.core.tamper_anchor import ObjectLockAnchor, StoredObject
from backend.app.db.models import AuditEvent
from backend.app.services import audit_anchor_service, audit_chain, audit_scrub, audit_service

_LITERAL = "PASTED_LITERAL"
_HELD = {"column": "status", "value_set": [_LITERAL]}
_CURRENT = {"column": "status", "value_set": ["open", "closed"]}


class _Anchor:
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []

    def publish(self, **_: Any) -> bool:
        return True

    def publish_scrub(self, receipt: dict[str, Any]) -> bool:
        self.receipts.append(receipt)
        return True


@pytest.fixture
def anchor(monkeypatch: pytest.MonkeyPatch) -> _Anchor:
    fake = _Anchor()
    monkeypatch.setattr(audit_scrub, "get_tamper_anchor", lambda: fake)
    return fake


def _event(session: Any, *, entity_type: str = "check", **payload: Any) -> AuditEvent:
    event = audit_service.record(
        session,
        action=f"{entity_type}.update",
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        actor=None,
        actor_kind="webhook",
        **payload,
    )
    session.commit()
    return event


def _tamper(session: Any, event: AuditEvent, **values: Any) -> None:
    """An out-of-band edit (the test role is a superuser, so the REVOKE does not stop it)."""
    sets = ", ".join(
        f"{k} = CAST(:{k} AS {'jsonb' if k in ('before', 'after') else 'text'})" for k in values
    )
    params = {k: json.dumps(v) if isinstance(v, dict) else v for k, v in values.items()}
    session.execute(
        text(f"UPDATE audit_events SET {sets} WHERE id = :id"), {**params, "id": event.id}
    )
    session.commit()
    session.expire_all()


def test_a_scrub_keeps_the_row_hash_and_the_chain_verifies(
    db_session: Any, anchor: _Anchor
) -> None:
    target = _event(db_session, before={"config": _HELD}, after={"config": _CURRENT})
    _event(db_session, after={"config": _CURRENT})  # a later row links to the target's hash
    row_hash = target.row_hash

    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="pasted by mistake")
    db_session.commit()
    db_session.refresh(target)

    assert target.before == {"config": {"column": "status"}}
    assert target.after == {"config": {"column": "status"}}
    assert target.row_hash == row_hash and target.scrubbed_at is not None
    assert audit_chain.compute_row_hash(target, target.prev_hash) != row_hash

    result = audit_chain.verify_chain(db_session)
    assert result.first_break is None
    assert result.scrubbed_count == 1

    record = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == audit_chain.SCRUB_ACTION)
    ).one()
    assert record.entity_id == target.id
    assert record.after["paths"] == ["before.config", "after.config"]
    assert record.after["target_row_hash"] == row_hash
    assert record.after["trigger"] == "admin" and record.after["reason"] == "pasted by mistake"
    assert _LITERAL not in json.dumps(record.after)

    assert [r["scrub_event_id"] for r in anchor.receipts] == [str(record.id)]
    assert anchor.receipts[0]["residual_hash"] == record.after["residual_hash"]


def test_a_rolled_back_scrub_publishes_no_receipt(db_session: Any, anchor: _Anchor) -> None:
    target = _event(db_session, after={"config": _HELD})
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.rollback()
    db_session.commit()
    assert anchor.receipts == []
    db_session.refresh(target)
    assert target.after == {"config": _HELD}


def test_an_edit_without_a_scrub_event_is_still_a_break(db_session: Any) -> None:
    target = _event(db_session, after={"config": _HELD})
    _tamper(db_session, target, after={"config": {"column": "status"}})
    result = audit_chain.verify_chain(db_session)
    assert result.first_break is not None and result.first_break.event_id == target.id


def test_a_column_changed_beside_a_scrub_is_a_break(db_session: Any, anchor: _Anchor) -> None:
    target = _event(db_session, after={"config": _HELD})
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.commit()
    _tamper(db_session, target, action="check.delete")
    result = audit_chain.verify_chain(db_session)
    assert result.first_break is not None and result.first_break.event_id == target.id


def test_a_tampered_row_is_never_scrubbed(db_session: Any, anchor: _Anchor) -> None:
    target = _event(db_session, after={"config": _HELD})
    _tamper(db_session, target, action="check.delete")
    with pytest.raises(audit_scrub.AuditEventUnverifiedError):
        audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")


@pytest.mark.parametrize(
    ("entity_type", "payload"),
    [
        ("connection", {"after": {"config": _HELD}}),
        ("check", {"after": {"name": "x"}}),
        ("check", {"after": {"config": {"column": "status"}}}),
    ],
)
def test_nothing_outside_the_allow_list_is_scrubbable(
    db_session: Any, entity_type: str, payload: dict[str, Any]
) -> None:
    target = _event(db_session, entity_type=entity_type, **payload)
    with pytest.raises(audit_scrub.AuditEventNotScrubbableError):
        audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")


def test_an_unknown_event_is_not_found(db_session: Any) -> None:
    with pytest.raises(audit_scrub.AuditEventNotFoundError):
        audit_scrub.scrub_event(db_session, uuid.uuid4(), actor=None, reason="r")


def test_a_second_scrub_of_the_other_side_still_verifies(db_session: Any, anchor: _Anchor) -> None:
    target = _event(db_session, before={"config": _HELD}, after={"config": _CURRENT})
    entity = target.entity_id
    assert entity is not None
    first = audit_scrub.scrub_matching(
        db_session,
        entity_type="check",
        entity_id=entity,
        key="config",
        value=_HELD,
        actor=None,
        reason="v1",
    )
    db_session.commit()
    second = audit_scrub.scrub_matching(
        db_session,
        entity_type="check",
        entity_id=entity,
        key="config",
        value=_CURRENT,
        actor=None,
        reason="v2",
    )
    db_session.commit()
    db_session.refresh(target)

    assert (first, second) == (1, 1)
    assert target.before == {"config": {"column": "status"}}
    assert target.after == {"config": {"column": "status"}}
    result = audit_chain.verify_chain(db_session)
    assert result.first_break is None and result.scrubbed_count == 1
    assert len(anchor.receipts) == 2


def test_the_match_is_on_the_value_as_the_payload_stores_it(
    db_session: Any, anchor: _Anchor
) -> None:
    """The audit payload scrubs credential-shaped keys and strings; the version snapshot keeps the
    raw config. A match on the raw value would never find the event it exists to clean.
    """
    held = {"column": "status", "password": "hunter2", "sql": "postgres://u:pw@db/x"}
    target = _event(db_session, after={"config": held})
    assert target.after is not None and target.after["config"] != held

    entity = target.entity_id
    assert entity is not None
    count = audit_scrub.scrub_matching(
        db_session,
        entity_type="check",
        entity_id=entity,
        key="config",
        value=held,
        actor=None,
        reason="r",
    )
    assert count == 1


def test_a_matching_scrub_skips_an_unverified_row_and_says_so(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _event(db_session, after={"config": _HELD})
    _tamper(db_session, target, action="check.delete")
    entity = target.entity_id
    assert entity is not None
    errors: list[str] = []
    monkeypatch.setattr(audit_scrub.log, "error", lambda event, **_: errors.append(event))
    count = audit_scrub.scrub_matching(
        db_session,
        entity_type="check",
        entity_id=entity,
        key="config",
        value=_HELD,
        actor=None,
        reason="r",
    )
    assert count == 0
    assert errors == ["audit_scrub_skipped_unverified"]
    db_session.refresh(target)
    assert target.after == {"config": _HELD}


# ───────────── receipts read back from an object-lock anchor ─────────────


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None:
        self.objects[key] = body

    def list_objects(self, prefix: str) -> list[StoredObject]:
        return [StoredObject(k) for k in self.objects if k.startswith(prefix)]

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        return [self.objects[o.key] for o in objects]


def _object_lock(monkeypatch: pytest.MonkeyPatch, store: _Store) -> ObjectLockAnchor:
    anchor = ObjectLockAnchor(store, prefix="audit-anchors", retention_days=30)
    monkeypatch.setattr(audit_scrub, "get_tamper_anchor", lambda: anchor)
    return anchor


def _publish_head(db_session: Any, anchor: ObjectLockAnchor, *, as_of: datetime) -> None:
    head = audit_chain.verify_chain(db_session).chain_head_hash
    assert head is not None
    anchor.publish(label="audit_chain_daily", head_hash=head, event_count=1, as_of=as_of)


def test_a_scrub_with_its_receipt_verifies(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store()
    anchor = _object_lock(monkeypatch, store)
    target = _event(db_session, after={"config": _HELD})
    _publish_head(db_session, anchor, as_of=datetime.now(UTC) - timedelta(minutes=5))
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.commit()

    assert any(k.startswith("audit-anchors-scrubs/") for k in store.objects)
    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.status == "ok"
    assert (result.scrub_receipt_count, result.unanchored_scrub_count) == (1, 0)
    assert result.checked_count == 1  # the receipt is not read as a head


def test_a_scrub_event_with_no_receipt_is_a_mismatch(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forged-scrub case: an actor with database access writes the scrub event themselves."""
    store = _Store()
    anchor = _object_lock(monkeypatch, store)
    target = _event(db_session, after={"config": _HELD})
    _publish_head(db_session, anchor, as_of=datetime.now(UTC) - timedelta(minutes=5))
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.commit()
    for key in [k for k in store.objects if k.startswith("audit-anchors-scrubs/")]:
        del store.objects[key]

    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.status == "mismatch"
    assert result.unanchored_scrub_count == 1
    scrub_id = db_session.scalars(
        select(AuditEvent.id).where(AuditEvent.action == audit_chain.SCRUB_ACTION)
    ).one()
    assert result.unanchored_scrub_event_ids == [str(scrub_id)]


def test_a_scrub_from_before_the_anchor_began_is_not_judged(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store()
    target = _event(db_session, after={"config": _HELD})
    monkeypatch.setattr(audit_scrub, "get_tamper_anchor", lambda: _Anchor())
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.commit()
    anchor = ObjectLockAnchor(store, prefix="audit-anchors", retention_days=30)
    _publish_head(db_session, anchor, as_of=datetime.now(UTC) + timedelta(minutes=5))

    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.status == "ok" and result.unanchored_scrub_count == 0


def test_a_receipt_for_no_scrub_event_is_an_orphan(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store()
    anchor = _object_lock(monkeypatch, store)
    _event(db_session, after={"config": _CURRENT})
    _publish_head(db_session, anchor, as_of=datetime.now(UTC))
    anchor.publish_scrub(
        {
            "label": "audit_events_scrub",
            "scrub_event_id": str(uuid.uuid4()),
            "as_of": datetime.now(UTC).isoformat(),
        }
    )
    store.objects["audit-anchors-scrubs/not-ours.json"] = b"{}"

    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.orphan_receipt_count == 2
    assert result.status == "ok"


# ───────────── forged scrub claims (an actor with direct database access) ─────────────


def _forged_claim(db_session: Any, target: AuditEvent, **row: Any) -> AuditEvent:
    """A scrub event the app never wrote, vouching for `target` exactly as it is now."""
    db_session.refresh(target)
    claim = AuditEvent(
        action_class="config",
        action=audit_chain.SCRUB_ACTION,
        entity_type=audit_chain.SCRUB_ENTITY_TYPE,
        entity_id=target.id,
        actor_kind="user",
        after={
            "target_row_hash": target.row_hash,
            "residual_hash": audit_chain.compute_row_hash(target, target.prev_hash),
        },
        **row,
    )
    db_session.add(claim)
    return claim


def test_an_unchained_claim_never_vouches_for_a_scrub(db_session: Any, anchor: _Anchor) -> None:
    """An altered row plus a hand-inserted claim must not let a real scrub launder it."""
    target = _event(db_session, after={"config": _HELD})
    _tamper(db_session, target, action="check.delete")
    _forged_claim(db_session, target, row_hash=None)
    db_session.flush()
    db_session.commit()

    with pytest.raises(audit_scrub.AuditEventUnverifiedError):
        audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.rollback()
    entity = target.entity_id
    assert entity is not None
    assert (
        audit_scrub.scrub_matching(
            db_session,
            entity_type="check",
            entity_id=entity,
            key="config",
            value=_HELD,
            actor=None,
            reason="r",
        )
        == 0
    )


def test_a_backdated_forged_scrub_is_judged_by_chain_position(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forged claim is chained at the head but claims a time before the anchor began."""
    store = _Store()
    anchor = _object_lock(monkeypatch, store)
    target = _event(db_session, after={"config": _HELD})
    _publish_head(db_session, anchor, as_of=datetime.now(UTC) - timedelta(minutes=5))
    _tamper(db_session, target, action="check.delete")
    forged = _forged_claim(db_session, target, occurred_at=datetime.now(UTC) - timedelta(days=30))
    db_session.info.setdefault(audit_chain.PENDING_CHAIN_KEY, []).append(forged)
    db_session.commit()

    chain = audit_chain.verify_chain(db_session)
    assert chain.first_break is None and chain.scrubbed_count == 1  # the chain alone is fooled
    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.status == "mismatch"
    assert result.unanchored_scrub_event_ids == [str(forged.id)]


def test_a_receipt_must_match_the_scrub_it_names(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real scrub with a receipt; then the target is altered further, the scrub event's residual
    rewritten to match and the unanchored tail rehashed. The receipt id still matches.
    """
    store = _Store()
    anchor = _object_lock(monkeypatch, store)
    target = _event(db_session, after={"config": _HELD})
    _publish_head(db_session, anchor, as_of=datetime.now(UTC) - timedelta(minutes=5))
    audit_scrub.scrub_event(db_session, target.id, actor=None, reason="r")
    db_session.commit()

    _tamper(db_session, target, action="check.delete")
    db_session.refresh(target)
    scrub = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == audit_chain.SCRUB_ACTION)
    ).one()
    _tamper(
        db_session,
        scrub,
        after={
            **scrub.after,
            "residual_hash": audit_chain.compute_row_hash(target, target.prev_hash),
        },
    )
    db_session.refresh(scrub)
    rehashed = audit_chain.compute_row_hash(scrub, scrub.prev_hash)
    _tamper(db_session, scrub, row_hash=rehashed)
    db_session.execute(text("UPDATE audit_chain_state SET head_hash = :h"), {"h": rehashed})
    db_session.commit()

    assert audit_chain.verify_chain(db_session).first_break is None  # the chain alone is fooled
    result = audit_anchor_service.verify_anchors(db_session, anchor)
    assert result is not None
    assert result.status == "mismatch"
    assert result.unanchored_scrub_event_ids == [str(scrub.id)]
