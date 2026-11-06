"""The automatic-coverage review queue (ADR 0047 §4)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.errors import DataQError
from backend.app.db.models import (
    Asset,
    AuditEvent,
    Check,
    CheckSuggestion,
    Connection,
    Suite,
    User,
)
from backend.app.services import profile_service
from backend.app.services import suggestion_service as svc
from backend.app.services.profile_service import ColumnProfile, ProfileResult


def _col(
    name: str, *, nulls: int = 0, distinct: int | None = None, top: list[Any] | None = None
) -> ColumnProfile:
    return ColumnProfile(
        column=name,
        null_count=nulls,
        null_fraction=0.0,
        distinct_count=distinct,
        min_value=None,
        max_value=None,
        top_values=[{"value": v, "count": 1} for v in (top or [])],
    )


def _names(rules: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(r["expectation_type"], r["config"]["column"]) for r in rules}


def test_rules_proposed_from_a_profile() -> None:
    profile = ProfileResult(
        row_count=500,
        columns=[
            _col("order_id", distinct=500),
            _col("created_at", distinct=500),
            _col("status", nulls=3, distinct=3, top=["pending", "shipped", "cancelled"]),
            _col("email", nulls=10, distinct=4, top=["a", "b", "c", "d"]),
        ],
    )
    rules = svc.rules_from_profile(profile, sensitive=lambda column, _values: column == "email")
    assert _names(rules) == {
        ("expect_column_values_to_not_be_null", "order_id"),
        ("expect_column_values_to_not_be_null", "created_at"),
        ("expect_column_values_to_be_unique", "order_id"),  # id-like only, not created_at
        ("expect_column_values_to_be_in_set", "status"),  # email is sensitive: no value set
    }
    in_set = next(r for r in rules if r["expectation_type"].endswith("in_set"))
    assert in_set["config"]["value_set"] == ["cancelled", "pending", "shipped"]


def test_too_few_rows_propose_nothing() -> None:
    profile = ProfileResult(row_count=40, columns=[_col("order_id", distinct=40)])
    assert svc.rules_from_profile(profile, sensitive=lambda *_: False) == []


@pytest.fixture
def suite(db_session: Any) -> tuple[Suite, User]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="admin")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "r"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    row = Suite(
        name="auto",
        connection_id=conn.id,
        created_by=owner.id,
        target={"schema": "public", "table": "orders"},
    )
    db_session.add(row)
    db_session.commit()
    return row, owner


def _propose(db_session: Any, suite: Suite, column: str = "order_id") -> bool:
    return svc.propose(
        db_session,
        suite,
        source="profile",
        name=f"{column} is never null",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": column},
        rationale="No nulls.",
    )


def test_a_rule_is_proposed_once_and_a_rejection_is_remembered(
    db_session: Any, suite: tuple[Suite, User]
) -> None:
    row, owner = suite
    assert _propose(db_session, row) is True
    assert _propose(db_session, row) is False
    pending = svc.list_suggestions(db_session, row.id, user_id=owner.id)
    svc.reject(db_session, pending[0].id, user_id=owner.id)
    assert _propose(db_session, row) is False
    assert svc.list_suggestions(db_session, row.id, user_id=owner.id) == []


def test_accepting_creates_the_check_and_records_who(
    db_session: Any, suite: tuple[Suite, User]
) -> None:
    row, owner = suite
    _propose(db_session, row)
    [pending] = svc.list_suggestions(db_session, row.id, user_id=owner.id)

    decided = svc.accept(db_session, pending.id, user_id=owner.id)

    check = db_session.get(Check, decided.check_id)
    assert check is not None and check.origin == "suggestion"
    assert check.expectation_type == "expect_column_values_to_not_be_null"
    assert (decided.status, decided.decided_by) == ("accepted", owner.id)
    actions = set(db_session.scalars(select(AuditEvent.action)))
    assert {"check.create", "suggestion.accept"} <= actions
    with pytest.raises(svc.SuggestionDecidedError):
        svc.reject(db_session, pending.id, user_id=owner.id)


def test_deciding_needs_edit_on_the_suite(db_session: Any, suite: tuple[Suite, User]) -> None:
    row, _ = suite
    _propose(db_session, row)
    stranger = User(
        aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="member"
    )
    db_session.add(stranger)
    db_session.commit()
    suggestion = db_session.scalar(select(CheckSuggestion))
    with pytest.raises(DataQError) as exc:
        svc.accept(db_session, suggestion.id, user_id=stranger.id)
    assert exc.value.status_code in (403, 404)
    assert db_session.scalar(select(Check).where(Check.suite_id == row.id)) is None


def test_refresh_from_profile_queues_the_rules(
    db_session: Any, suite: tuple[Suite, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    row, owner = suite
    monkeypatch.setattr(svc, "_classify_before_proposing", lambda *a, **kw: None)
    monkeypatch.setattr(profile_service, "list_columns", lambda *a, **kw: ["order_id"])
    monkeypatch.setattr(
        profile_service,
        "profile_connection",
        lambda *a, **kw: ProfileResult(row_count=500, columns=[_col("order_id", distinct=500)]),
    )
    assert svc.refresh_from_profile(db_session, row, secret_store=None) == 2  # type: ignore[arg-type]
    assert (row.auto_state or {}).get("profiled_at")
    assert len(svc.list_suggestions(db_session, row.id, user_id=owner.id)) == 2


@pytest.mark.parametrize(
    ("column", "values", "policy"),
    [
        ("contact", ["ana@example.com", "bo@example.com"], None),  # the values look like PII
        ("tier", ["gold", "silver"], {"pii_columns": ["tier"]}),  # the suite's policy says so
        ("tier", ["gold", "silver"], {"require_classification": True}),  # fail-closed, unclassified
    ],
)
def test_a_sensitive_value_set_is_never_proposed(
    db_session: Any, suite: tuple[Suite, User], column: str, values: list[str], policy: Any
) -> None:
    row, _ = suite
    row.column_policy = policy
    db_session.commit()
    profile = ProfileResult(row_count=500, columns=[_col(column, nulls=1, distinct=2, top=values)])
    rules = svc.rules_from_profile(profile, sensitive=svc.value_set_is_sensitive(db_session, row))
    assert rules == []


def test_an_ordinary_value_set_is_proposed(db_session: Any, suite: tuple[Suite, User]) -> None:
    row, _ = suite
    profile = ProfileResult(
        row_count=500, columns=[_col("tier", nulls=1, distinct=2, top=["gold", "silver"])]
    )
    rules = svc.rules_from_profile(profile, sensitive=svc.value_set_is_sensitive(db_session, row))
    assert [r["config"]["value_set"] for r in rules] == [["gold", "silver"]]


# ── a value set re-screened at read time (#2383) ──────────────────────────────


def _propose_value_set(
    db_session: Any, suite: Suite, *, source: str = "profile"
) -> CheckSuggestion:
    svc.propose(
        db_session,
        suite,
        source=source,
        name="tier is gold or silver" if source == "llm" else "tier is one of 2 values",
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "tier", "value_set": ["gold", "silver"]},
        rationale="only gold and silver seen" if source == "llm" else "Only these 2 values.",
    )
    db_session.commit()
    suggestion: CheckSuggestion | None = db_session.scalar(
        select(CheckSuggestion).where(
            CheckSuggestion.suite_id == suite.id, CheckSuggestion.source == source
        )
    )
    assert suggestion is not None
    return suggestion


def _classify_later(db_session: Any, suite: Suite, how: str) -> None:
    if how == "policy":
        suite.column_policy = {"pii_columns": ["tier"]}
    else:
        asset = Asset(
            namespace="postgres://h/shop",
            name="public.orders",
            column_tags={"tier": "pii"},
        )
        db_session.add(asset)
        db_session.flush()
        suite.asset_id = asset.id
    db_session.commit()


@pytest.mark.parametrize(("how", "reason"), [("policy", "policy"), ("tag", "own_tag")])
def test_a_value_set_classified_after_it_was_proposed_is_withheld(
    db_session: Any, suite: tuple[Suite, User], how: str, reason: str
) -> None:
    row, owner = suite
    suggestion = _propose_value_set(db_session, row)
    [before] = svc.present(db_session, [suggestion])
    assert before.config["value_set"] == ["gold", "silver"] and before.redacted_columns == []

    _classify_later(db_session, row, how)
    [view] = svc.present(db_session, svc.list_suggestions(db_session, row.id, user_id=owner.id))
    assert view.config == {"column": "tier", "value_set": ["<redacted>", "<redacted>"]}
    assert (view.redacted_columns, view.redaction_reasons) == (
        ["tier"],
        {"tier": reason},
    )
    assert (view.name, view.rationale) == (
        "tier is one of 2 values",
        "Only these 2 values.",
    )
    # The stored row is untouched; only the read is masked.
    db_session.refresh(suggestion)
    assert suggestion.config["value_set"] == ["gold", "silver"]


def test_an_ai_rules_wording_is_withheld_with_its_values(
    db_session: Any, suite: tuple[Suite, User]
) -> None:
    row, _ = suite
    suggestion = _propose_value_set(db_session, row, source="llm")
    _classify_later(db_session, row, "policy")
    [view] = svc.present(db_session, [suggestion])
    assert (view.name, view.rationale) == ("tier: value set withheld", None)
    assert "gold" not in repr(view)


def test_accepting_a_now_sensitive_value_set_is_refused(
    db_session: Any, suite: tuple[Suite, User]
) -> None:
    row, owner = suite
    suggestion = _propose_value_set(db_session, row)
    _classify_later(db_session, row, "tag")
    with pytest.raises(svc.SuggestionSensitiveError) as exc:
        svc.accept(db_session, suggestion.id, user_id=owner.id)
    assert exc.value.status_code == 409
    assert exc.value.detail == {"column": "tier", "reason": "own_tag"}
    assert db_session.scalar(select(Check).where(Check.suite_id == row.id)) is None
    db_session.refresh(suggestion)
    assert suggestion.status == "pending"
    # Rejecting it is still possible.
    assert svc.reject(db_session, suggestion.id, user_id=owner.id).status == "rejected"
