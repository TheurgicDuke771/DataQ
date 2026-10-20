"""The profiler classification source — ADR 0049 (#1674): detect, persist, overlay, dismiss."""

from __future__ import annotations

import base64
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.db.models import Asset, AssetColumnClassification, AuditEvent, User
from backend.app.services import column_detections as cd
from backend.app.services import column_tags as ct

_T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _col(name: str, values: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(column=name, top_values=[{"value": v, "count": 1} for v in values])


_EMAILS = [f"user{i}@example.com" for i in range(10)]
_UUIDS = [str(uuid.uuid4()) for _ in range(10)]
# Random base64, not hex: a hex string of a digest length is an IDENTIFIER (a hash), by design.
_BLOBS = [base64.b64encode(secrets.token_bytes(24)).decode() for _ in range(10)]


def _asset(db_session: Any, **kw: Any) -> Asset:
    asset = Asset(namespace="postgres://x", name=f"public.t_{uuid.uuid4().hex[:8]}", **kw)
    db_session.add(asset)
    db_session.flush()
    return asset


def _rows(db_session: Any, asset: Asset) -> list[AssetColumnClassification]:
    return list(
        db_session.scalars(
            select(AssetColumnClassification).where(AssetColumnClassification.asset_id == asset.id)
        )
    )


# ── detect: value-based, the two detectors, and nothing else ────────────────────────────────


def test_detect_fires_on_email_and_encoded_values_only() -> None:
    found = {
        d.column: d.detector
        for d in cd.detect(
            [
                _col("contact", _EMAILS),
                _col("payload", _BLOBS),
                _col("surrogate", _UUIDS),  # id-shaped is an IDENTIFIER, never a detection
                _col("status", ["new", "paid", "shipped"]),
                _col("empty", [None, "", "NULL"]),
            ]
        )
    }
    assert found == {"contact": "email", "payload": "encoded"}


def test_detect_is_value_based_so_a_pii_name_with_plain_values_is_not_a_detection() -> None:
    """A name signal is applied live at every rung already; persisting it adds nothing."""
    assert cd.detect([_col("email", ["n/a", "unknown", "-"])]) == []


def test_evidence_holds_counts_and_never_a_value() -> None:
    (d,) = cd.detect([_col("contact", _EMAILS)])
    assert set(d.evidence) == {
        "n",
        "email_count",
        "id_shaped_count",
        "encoded_count",
        "distinct_count",
    }
    assert all(isinstance(v, int) for v in d.evidence.values())
    assert not any("@" in str(v) for v in d.evidence.values())


# ── record: new, re-seen, dismissed, unseen, empty ───────────────────────────────────────────


def test_record_persists_new_detections_as_sensitive_and_stamps_the_asset(db_session: Any) -> None:
    asset = _asset(db_session)
    created = cd.record_profile_detections(
        db_session, asset, [_col("Contact", _EMAILS), _col("status", ["a", "b"])], now=_T0
    )
    (row,) = _rows(db_session, asset)
    assert created == 1
    assert (row.column_name, row.source, row.verdict, row.detector) == (
        "Contact",
        "profiler",
        "sensitive",
        "email",
    )
    assert row.first_detected_at == row.last_detected_at == _T0
    assert row.dismissed_at is None
    assert asset.detected_classifications_refreshed_at == _T0


def test_a_profile_that_finds_nothing_still_stamps_looked(db_session: Any) -> None:
    """ "Looked and found none" must differ from "never looked"."""
    asset = _asset(db_session)
    assert cd.record_profile_detections(db_session, asset, [_col("status", ["a"])], now=_T0) == 0
    assert _rows(db_session, asset) == []
    assert asset.detected_classifications_refreshed_at == _T0


def test_re_detection_keeps_first_seen_and_moves_last_seen(db_session: Any) -> None:
    asset = _asset(db_session)
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)
    later = _T0 + timedelta(days=1)
    created = cd.record_profile_detections(
        db_session, asset, [_col("CONTACT", _EMAILS)], now=later  # the warehouse re-cases it
    )
    (row,) = _rows(db_session, asset)
    assert created == 0
    assert (row.first_detected_at, row.last_detected_at) == (_T0, later)


def test_a_column_not_seen_by_a_later_profile_is_left_alone(db_session: Any) -> None:
    """Absence from the top values proves little (ADR 0049 §4) — no quiet un-masking."""
    asset = _asset(db_session)
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)
    cd.record_profile_detections(
        db_session, asset, [_col("contact", ["n/a"] * 10)], now=_T0 + timedelta(days=1)
    )
    (row,) = _rows(db_session, asset)
    assert row.dismissed_at is None and row.last_detected_at == _T0
    assert cd.active_detected_columns(db_session, asset) == ["contact"]


def test_a_dismissal_survives_re_detection(db_session: Any) -> None:
    asset = _asset(db_session)
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)
    cd.set_dismissed(db_session, asset, "contact", dismissed=True, actor_id=None)
    later = _T0 + timedelta(days=2)
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=later)
    (row,) = _rows(db_session, asset)
    assert row.dismissed_at is not None, "the steward's decision stands"
    assert row.last_detected_at == later, "…and the page can still say 'still detected'"
    assert cd.active_detected_columns(db_session, asset) == []


def test_the_safe_wrapper_swallows_a_persistence_failure(db_session: Any, monkeypatch: Any) -> None:
    asset = _asset(db_session)
    db_session.commit()  # the wrapper rolls back; the asset must already be durable

    def _boom(*a: Any, **k: Any) -> int:
        raise RuntimeError("db down")

    monkeypatch.setattr(cd, "record_profile_detections", _boom)
    cd.record_profile_detections_safely(db_session, asset, [_col("contact", _EMAILS)])  # no raise
    assert db_session.get(Asset, asset.id) is not None  # the session is still usable


# ── the overlay: where the rung sits in the map ──────────────────────────────────────────────


def test_an_active_detection_fills_a_column_with_no_warehouse_verdict(db_session: Any) -> None:
    asset = _asset(db_session, column_tags={"ssn": ct.SENSITIVE})
    cd.record_profile_detections(db_session, asset, [_col("Contact", _EMAILS)], now=_T0)
    assert ct.effective_column_tags(db_session, asset) == {
        "ssn": ct.SENSITIVE,
        "contact": ct.DETECTED,
    }


@pytest.mark.parametrize("own", [ct.SENSITIVE, ct.NON_SENSITIVE])
def test_a_warehouse_verdict_on_the_same_column_wins_either_way(db_session: Any, own: str) -> None:
    """The steward looked at this column; a heuristic never overrides them — not even to mask."""
    asset = _asset(db_session, column_tags={"contact": own})
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)
    assert ct.effective_column_tags(db_session, asset) == {"contact": own}


def test_a_dismissed_detection_is_not_in_the_map(db_session: Any) -> None:
    asset = _asset(db_session)
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)
    cd.set_dismissed(db_session, asset, "contact", dismissed=True, actor_id=None)
    assert ct.effective_column_tags(db_session, asset) is None
    cd.set_dismissed(db_session, asset, "contact", dismissed=False, actor_id=None)
    assert ct.effective_column_tags(db_session, asset) == {"contact": ct.DETECTED}


def test_an_asset_with_no_verdict_from_any_source_keeps_the_never_looked_shape(
    db_session: Any,
) -> None:
    assert ct.effective_column_tags(db_session, _asset(db_session)) is None
    assert ct.effective_column_tags(db_session, _asset(db_session, column_tags={})) == {}


def test_a_failing_detection_read_falls_back_to_the_stronger_rungs(
    db_session: Any, monkeypatch: Any
) -> None:
    asset = _asset(db_session, column_tags={"ssn": ct.SENSITIVE})
    cd.record_profile_detections(db_session, asset, [_col("contact", _EMAILS)], now=_T0)

    def _boom(*a: Any, **k: Any) -> list[str]:
        raise RuntimeError("table missing")

    monkeypatch.setattr(cd, "active_detected_columns", _boom)
    assert ct.effective_column_tags(db_session, asset) == {"ssn": ct.SENSITIVE}
    # …and the caller's session survived the failure.
    assert db_session.get(Asset, asset.id) is asset


def test_the_detected_value_is_neither_a_floor_nor_a_clearance() -> None:
    """The whole design rests on the third value staying out of both tag sets."""
    from backend.app.services.run_service import _NON_SENSITIVE_TAG_VALUES, _SENSITIVE_TAG_VALUES

    assert ct.DETECTED not in _SENSITIVE_TAG_VALUES
    assert ct.DETECTED not in _NON_SENSITIVE_TAG_VALUES
    assert (
        ct.normalize_tag(ct.DATAQ_TAG_KEY, ct.DETECTED) is None
    ), "a warehouse tag literally valued 'detected' must not impersonate the rung"


# ── dismissal: audited, idempotent, 404 on an unknown column ────────────────────────────────


def _audit_rows(db_session: Any, asset: Asset) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_id == asset.id, AuditEvent.action == "asset.classification_update"
            )
            .order_by(AuditEvent.occurred_at)
        )
    )


def test_dismiss_and_restore_are_audited_with_the_actor(db_session: Any) -> None:
    actor = User(aad_object_id=uuid.uuid4().hex, email="steward@acme.io")
    db_session.add(actor)
    asset = _asset(db_session)
    cd.record_profile_detections(db_session, asset, [_col("Contact", _EMAILS)], now=_T0)

    row = cd.set_dismissed(db_session, asset, "CONTACT", dismissed=True, actor_id=actor.id)
    assert row.dismissed_by == actor.id and row.dismissed_at is not None
    cd.set_dismissed(db_session, asset, "contact", dismissed=True, actor_id=actor.id)  # no-op
    cd.set_dismissed(db_session, asset, "contact", dismissed=False, actor_id=actor.id)

    events = _audit_rows(db_session, asset)
    assert [(e.before, e.after) for e in events] == [
        ({"column": "Contact", "dismissed": False}, {"column": "Contact", "dismissed": True}),
        ({"column": "Contact", "dismissed": True}, {"column": "Contact", "dismissed": False}),
    ], "a no-op repeat writes no event"
    assert all(e.actor_user_id == actor.id and e.entity_type == "asset" for e in events)


def test_dismissing_an_unknown_column_is_a_404(db_session: Any) -> None:
    asset = _asset(db_session)
    with pytest.raises(cd.ClassificationNotFoundError) as exc:
        cd.set_dismissed(db_session, asset, "nothing", dismissed=True, actor_id=None)
    assert exc.value.status_code == 404


def test_list_classifications_includes_dismissed_rows_by_column(db_session: Any) -> None:
    asset = _asset(db_session)
    cd.record_profile_detections(
        db_session, asset, [_col("zeta", _EMAILS), _col("Alpha", _BLOBS)], now=_T0
    )
    cd.set_dismissed(db_session, asset, "zeta", dismissed=True, actor_id=None)
    rows = cd.list_classifications(db_session, asset.id)
    assert [(r.column_name, r.dismissed_at is not None) for r in rows] == [
        ("Alpha", False),
        ("zeta", True),
    ]
