"""Unit tests for the audit seam — ADR 0041 phase 1 (#1318)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import _PII_KEYS
from backend.app.db.models import AUDIT_ACTOR_KINDS, AuditEvent, Check, Connection, Suite, User
from backend.app.services import audit_service


class _FakeSession:
    """Captures `add` without a database — `record` must not commit or flush."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False
        self.flushed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:  # pragma: no cover - failing this is the point
        self.committed = True

    def flush(self) -> None:  # pragma: no cover - failing this is the point
        self.flushed = True


def _as_session(fake: _FakeSession) -> Session:
    """`_FakeSession` deliberately implements only `add`/`commit`/`flush` — the three methods whose
    (non-)use IS the contract under test. Casting rather than subclassing `Session` keeps that
    surface honest: a subclass would inherit a hundred real methods and quietly let a future
    `record` reach the database without any test noticing.
    """
    return cast(Session, fake)


class _Actor:
    def __init__(self, display_name: str | None = None, email: str = "a@example.com") -> None:
        self.id = uuid.uuid4()
        self.display_name = display_name
        self.email = email


# ── The drift guard (ADR 0041 §2.6.4) ─────────────────────────────────────────


def test_credential_keys_are_a_subset_of_the_logger_pii_keys() -> None:
    """The audit scrubber reuses the CREDENTIAL subset of the logger's key set."""
    assert audit_service._CREDENTIAL_KEYS <= _PII_KEYS


def test_credential_keys_deliberately_exclude_the_log_line_pii_keys() -> None:
    """`name`, `display_name`, `user_id` and `email` are in `_PII_KEYS` and must
    NOT be reused here.
    """
    for key in ("name", "display_name", "user_id", "email"):
        assert key in _PII_KEYS
        assert key not in audit_service._CREDENTIAL_KEYS


# ── What cannot reach a payload ───────────────────────────────────────────────


def test_undeclared_entity_type_raises_rather_than_guessing() -> None:
    """A new audited surface with no declared allow-list must FAIL, not fall back
    to `dict(row)` or to an empty payload.
    """
    with pytest.raises(audit_service.UnknownAuditEntityError):
        audit_service.snapshot("no_such_entity", object())


def test_connection_payload_carries_the_pointer_and_never_the_config_blob() -> None:
    """A connection's `config` JSONB holds every `*_secret_name` pointer and every adapter-specific
    field — it is the one place a future adapter could put something credential-shaped without
    this module noticing — so it is excluded wholesale, while `secret_ref` (the pointer that
    makes a `reauth` event meaningful) is kept.
    """
    conn = Connection(
        id=uuid.uuid4(),
        name="warehouse",
        type="snowflake",
        env="dev",
        secret_ref="conn-snowflake-orders-dev-ab12",
        config={
            "account": "acme",
            "password": "hunter2-should-never-appear",
            "catalog_secret_name": "conn-iceberg-cat-dev-cd34",
        },
    )
    payload = audit_service.snapshot("connection", conn)
    assert payload is not None
    assert payload["secret_ref"] == "conn-snowflake-orders-dev-ab12"
    assert "config" not in payload
    assert "hunter2-should-never-appear" not in json.dumps(payload)


def test_a_credential_keyed_field_is_scrubbed_even_if_a_serializer_lets_it_through() -> None:
    """Belt and braces: the allow-list is the primary control, `_scrub_value` is the second one,
    and it must work on a nested structure — an audit payload is a JSONB write that NEVER passes
    through structlog, so CLAUDE.md §10's redact-at-the-logger rule genuinely does not cover it
    (the #849 shape in the one place the project's own rule does not reach).
    """
    scrubbed = audit_service._scrub_value(
        {"outer": {"password": "s3cret", "keep": "visible", "list": [{"token": "abc"}]}}
    )
    assert scrubbed["outer"]["password"] == audit_service._REDACTED
    assert scrubbed["outer"]["keep"] == "visible"
    assert scrubbed["outer"]["list"][0]["token"] == audit_service._REDACTED


def test_a_credential_embedded_in_a_string_is_scrubbed() -> None:
    """The key-based pass only sees KEYS. A credential smuggled inside a *value*
    — a webhook URL's `?token=`, a URL's userinfo — is the other half, and it is
    the shape #494/#536 actually hit in production.
    """
    scrubbed = audit_service._scrub_value(
        {"url": "https://example.com/events/adf?token=live-secret-value"}
    )
    assert "live-secret-value" not in scrubbed["url"]


def test_suite_payload_carries_the_redaction_policy_and_no_warehouse_data() -> None:
    """`column_policy` is in the payload deliberately — a change to `pii_columns`
    changes what personal data the product will surface, which is among the
    highest-value config events in the table. Nothing else about the suite's data
    is.
    """
    suite = Suite(id=uuid.uuid4(), name="orders", column_policy={"pii_columns": ["email"]})
    payload = audit_service.snapshot("suite", suite)
    assert payload is not None
    assert payload["column_policy"] == {"pii_columns": ["email"]}
    for forbidden in ("sample_failures", "observed_value"):
        assert forbidden not in payload


def test_check_thresholds_survive_as_exact_strings_not_floats() -> None:
    """NUMERIC thresholds arrive as `Decimal`, which the JSON encoder rejects —
    the #1273 class exactly, where a legitimately-failed check crashed the whole
    result insert because `sanitize_json` had no `Decimal` branch and a passing
    check never exercised the path.
    """
    check = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="row count",
        kind="expectation",
        expectation_type="expect_table_row_count_to_be_between",
        config={"min_value": 1},
        warn_threshold=Decimal("0.1"),
    )
    payload = audit_service.snapshot("check", check)
    assert payload is not None
    assert payload["warn_threshold"] == "0.1"
    json.dumps(payload)  # must not raise


# ── The payload cap ───────────────────────────────────────────────────────────


def test_an_oversized_payload_is_capped_with_a_LOUD_marker() -> None:
    """No silent caps (ADR 0040 §5). A reader must be able to tell "this field was
    absent" from "this field was dropped for size" — a silent cap reads as a
    complete record, which is the failure this table exists to prevent.
    """
    suite = Suite(
        id=uuid.uuid4(),
        name="orders",
        description="x" * (audit_service.MAX_PAYLOAD_BYTES * 2),
        column_policy={"pii_columns": ["email"]},
    )
    payload = audit_service.snapshot("suite", suite)
    assert payload is not None
    marker = payload[audit_service._TRUNCATION_KEY]
    assert marker["dropped_fields"] == ["description"]
    assert marker["limit_bytes"] == audit_service.MAX_PAYLOAD_BYTES
    # Dropping the LARGEST field first is what keeps the record informative: the
    # small fields beside the giant one are usually the interesting part.
    assert payload["name"] == "orders"
    assert payload["column_policy"] == {"pii_columns": ["email"]}
    assert len(json.dumps(payload).encode()) <= audit_service.MAX_PAYLOAD_BYTES * 1.1


def test_a_payload_under_the_cap_carries_no_truncation_marker() -> None:
    """The other half — the marker must mean something. If it were always present
    a reader would learn to ignore it.
    """
    suite = Suite(id=uuid.uuid4(), name="orders")
    payload = audit_service.snapshot("suite", suite)
    assert payload is not None
    assert audit_service._TRUNCATION_KEY not in payload


# ── The write contract ────────────────────────────────────────────────────────


def test_record_adds_to_the_caller_transaction_and_never_commits() -> None:
    """Fail-closed (ADR 0041 §2.1): the event and the mutation it records commit
    together or not at all. If `record` committed on its own, an audit row could
    survive a rolled-back mutation — a record of something that never happened,
    which is worse than no record.
    """
    session = _FakeSession()
    audit_service.record(
        _as_session(session),
        action="check.delete",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor=_Actor(display_name="Olivia"),
    )
    assert len(session.added) == 1
    assert session.committed is False
    assert session.flushed is False


def test_actor_label_is_denormalized_at_write_time() -> None:
    """`actor_user_id` is `ON DELETE SET NULL`, so the label is the only thing
    keeping the event legible after the actor is erased — and it must be the
    identity *as at the time of the action*, not a later rename.
    """
    session = _FakeSession()
    actor = _Actor(display_name="Olivia Green")
    audit_service.record(
        _as_session(session),
        action="share.grant",
        entity_type="share",
        entity_id=uuid.uuid4(),
        actor=actor,
    )
    event = session.added[0]
    assert event.actor_label == "Olivia Green"
    assert event.actor_user_id == actor.id


def test_actor_label_falls_back_to_email_when_there_is_no_display_name() -> None:
    session = _FakeSession()
    audit_service.record(
        _as_session(session),
        action="user.provision",
        entity_type="user",
        entity_id=uuid.uuid4(),
        actor=_Actor(display_name=None, email="olivia@example.com"),
    )
    assert session.added[0].actor_label == "olivia@example.com"


def test_a_webhook_principal_has_no_user_and_that_is_representable() -> None:
    """An orchestrator webhook is an external principal with no `users` row. It
    must be recordable *as such* rather than forced into a fake user.
    """
    session = _FakeSession()
    audit_service.record(
        _as_session(session),
        action="trigger_binding.fire",
        entity_type="trigger_binding",
        entity_id=uuid.uuid4(),
        actor=None,
        actor_kind="webhook",
    )
    event = session.added[0]
    assert event.actor_user_id is None
    assert event.actor_label is None
    assert event.actor_kind == "webhook"


def test_there_is_no_system_actor_kind() -> None:
    """ADR 0041 §2.1: machine writes are out of scope entirely, so a `system` actor would have no
    legitimate producer and would exist purely as an invitation to smuggle routine machine
    writes in under it. Asserted at the boundary, so a caller cannot introduce one without also
    widening the vocabulary deliberately.
    """
    assert "system" not in AUDIT_ACTOR_KINDS
    with pytest.raises(ValueError, match="no 'system' kind"):
        audit_service.record(
            _as_session(_FakeSession()),
            action="check.update",
            entity_type="check",
            entity_id=uuid.uuid4(),
            actor=None,
            actor_kind="system",
        )


def test_record_entity_change_recovers_the_id_from_a_delete_before_payload() -> None:
    """A delete event has no entity to read an id from — and the delete is the one
    event a Type-4 snapshot table structurally cannot retain, so losing the id
    here would defeat the table's headline purpose.
    """
    session = _FakeSession()
    suite_id = uuid.uuid4()
    before = audit_service.snapshot("suite", Suite(id=suite_id, name="orders"))
    audit_service.record_entity_change(
        _as_session(session),
        action="suite.delete",
        entity_type="suite",
        entity=None,
        actor=_Actor(),
        before=before,
    )
    event = session.added[0]
    assert event.entity_id == suite_id
    assert event.after is None
    assert event.before is not None and event.before["name"] == "orders"


def test_snapshot_of_none_is_none_not_an_empty_dict() -> None:
    """So a create event's `before` and a delete event's `after` read as "there
    was nothing", not as "we looked and it was blank".
    """
    assert audit_service.snapshot("suite", None) is None


# ── Review findings (PR #1451) ──────────────────────────────────────────────── Each of these five
# failed against the code as first written.


def test_a_create_is_audited_with_the_id_the_database_assigned(db_session: Any) -> None:
    """`id` is `gen_random_uuid()` — a SERVER default with no Python-side
    counterpart — so on a create the attribute is `None` until the row reaches the
    database.
    """
    owner = User(email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a", "warehouse": "w", "database": "d", "role": "r"},
        secret_ref="kv-ref",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    # NOT flushed — `suite.id` is None right now, which is the whole point.
    assert suite.id is None

    event = audit_service.record_entity_change(
        db_session,
        action="suite.create",
        entity_type="suite",
        entity=suite,
        actor=None,
        actor_kind="webhook",
    )

    assert suite.id is not None
    assert event.entity_id == suite.id
    assert event.after is not None and event.after["id"] == str(suite.id)


def test_an_undeclared_entity_type_raises_on_the_DELETE_path_too() -> None:
    """The delete path passes `entity=None`, and an earlier version short-circuited
    on that before ever consulting the allow-list — so a typo'd `entity_type` was
    recorded silently there while the same typo raised on every other path.
    """
    with pytest.raises(audit_service.UnknownAuditEntityError):
        audit_service.snapshot("no_such_entity", None)


def test_a_hand_built_payload_is_scrubbed_like_a_snapshot_one() -> None:
    """The module's guarantees must hold on EVERY payload, not only the ones `snapshot` built."""
    session = _FakeSession()
    audit_service.record(
        _as_session(session),
        action="connection.reauth",
        entity_type="connection",
        entity_id=uuid.uuid4(),
        actor=_Actor(),
        after={"password": "hunter2", "secret_ref": "conn-snowflake-orders-dev-ab12"},
    )
    event = session.added[0]
    assert event.after["password"] == audit_service._REDACTED
    assert event.after["secret_ref"] == "conn-snowflake-orders-dev-ab12"


def test_a_hand_built_payload_with_a_decimal_does_not_crash_the_mutation() -> None:
    """The #1273 class, arriving by the other door. A stray `Decimal` in a
    caller-built payload reaches the JSONB encoder and raises — and because this
    write is same-transaction and fail-closed, that failure rolls back the USER'S
    mutation. The audit log must never be the reason a legitimate change fails.
    """
    session = _FakeSession()
    audit_service.record(
        _as_session(session),
        action="check.update",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor=_Actor(),
        after={"warn_threshold": Decimal("0.25")},
    )
    json.dumps(session.added[0].after)  # must not raise
    assert session.added[0].after["warn_threshold"] == "0.25"


def test_an_invalid_action_class_raises_at_the_call_site() -> None:
    """`actor_kind` was validated and `action_class` was not, though both carry a DB CHECK."""
    with pytest.raises(ValueError, match="action_class"):
        audit_service.record(
            _as_session(_FakeSession()),
            action="check.update",
            entity_type="check",
            entity_id=uuid.uuid4(),
            actor=None,
            actor_kind="webhook",
            action_class="not_a_class",
        )


def test_a_truncated_payload_INCLUDING_its_marker_fits_the_budget() -> None:
    """The marker is part of the payload, so it is part of the budget."""
    limit = audit_service.MAX_PAYLOAD_BYTES
    survivor = "y" * (limit - len(json.dumps({"keep": ""}).encode()) - 8)
    payload = {"drop_me": "x" * limit * 2, "keep": survivor}
    assert audit_service._encoded_size({"keep": survivor}) <= limit
    assert audit_service._encoded_size({"keep": survivor}) > limit - 64  # marker-sized headroom

    capped = audit_service._cap_payload(payload)

    assert capped[audit_service._TRUNCATION_KEY]["dropped_fields"] == ["drop_me", "keep"]
    assert audit_service._encoded_size(capped) <= limit


def test_a_payload_whose_single_field_blows_the_budget_reduces_to_its_marker() -> None:
    """The honest outcome when ONE field is oversized: the row says "there was a
    record and this is why you cannot see it".
    """
    capped = audit_service._cap_payload(
        {"sql": "SELECT 1 -- " + "x" * audit_service.MAX_PAYLOAD_BYTES * 2}
    )
    assert list(capped) == [audit_service._TRUNCATION_KEY]
    assert capped[audit_service._TRUNCATION_KEY]["dropped_fields"] == ["sql"]
    assert audit_service._encoded_size(capped) <= audit_service.MAX_PAYLOAD_BYTES


# ── Review findings (PR #1454) ────────────────────────────────────────────────


def test_an_unresolvable_actor_id_does_not_write_a_dangling_foreign_key(
    db_session: Any,
) -> None:
    """`actor_user_id` is a real FK, so an id with no row fails the INSERT."""
    ghost = uuid.uuid4()
    audit_service.record(
        db_session,
        action="suite.delete",
        entity_type="suite",
        entity_id=uuid.uuid4(),
        actor=ghost,
    )
    db_session.flush()  # the INSERT the FK would reject

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "suite.delete")
    ).first()
    assert event is not None
    assert event.actor_user_id is None
    assert str(ghost) in (event.actor_label or "")


def test_if_changed_skips_a_no_op_update() -> None:
    """A PATCH that sets a field to its current value — or names no fields at all
    — is a real request that changed nothing.
    """
    suite = Suite(id=uuid.uuid4(), name="orders")
    unchanged = audit_service.snapshot("suite", suite)

    session = _FakeSession()
    result = audit_service.record_entity_change(
        _as_session(session),
        action="suite.update",
        entity_type="suite",
        entity=suite,
        actor=None,
        actor_kind="webhook",
        before=unchanged,
        if_changed=True,
    )
    assert result is None
    assert session.added == []


def test_if_changed_still_writes_when_something_actually_changed() -> None:
    """The other half — the guard must not be a mute button."""
    suite = Suite(id=uuid.uuid4(), name="orders")
    before = audit_service.snapshot("suite", suite)
    suite.name = "orders-renamed"

    session = _FakeSession()
    result = audit_service.record_entity_change(
        _as_session(session),
        action="suite.update",
        entity_type="suite",
        entity=suite,
        actor=None,
        actor_kind="webhook",
        before=before,
        if_changed=True,
    )
    assert result is not None
    assert session.added[0].after["name"] == "orders-renamed"


def test_if_changed_ignores_a_change_to_an_UNAUDITED_field() -> None:
    """The comparison is over the allow-listed payload, not the row."""
    suite = Suite(id=uuid.uuid4(), name="orders")
    before = audit_service.snapshot("suite", suite)
    suite.updated_at = datetime(2026, 8, 17, tzinfo=UTC)  # not in _SUITE_FIELDS

    session = _FakeSession()
    result = audit_service.record_entity_change(
        _as_session(session),
        action="suite.update",
        entity_type="suite",
        entity=suite,
        actor=None,
        actor_kind="webhook",
        before=before,
        if_changed=True,
    )
    assert result is None
