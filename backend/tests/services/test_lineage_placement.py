"""Suggestion placement / dedup advice from column lineage (#1710).

Driven by the CAPTURED Unity Catalog payload through the real provider + real refresh (the
`captured` fixture), so the pairs the advice walks are the warehouse's own:
raw.feedback → silver.feedback → gold.feedback_sentiment, with `customer_id` passed through
unchanged and `sentiment` derived from `comment`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.models import Check, Connection, Share, Suite, User
from backend.app.services import lineage_placement as lp
from backend.tests.lineage.test_columns import _asset_id, captured, uc_connection  # noqa: F401

UNIQUE = "expect_column_values_to_be_unique"
NOT_NULL = "expect_column_values_to_not_be_null"


def _user(db_session: Session) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"p-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    return user


def _suite(db_session: Session, conn: Connection, owner: User, asset_id: uuid.UUID) -> Suite:
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    suite.asset_id = asset_id
    db_session.add(suite)
    db_session.flush()
    return suite


def _check(db_session: Session, suite: Suite, etype: str, column: str) -> Check:
    check = Check(
        suite_id=suite.id,
        name=f"{etype}:{column}",
        kind="expectation",
        expectation_type=etype,
        config={"column": column},
    )
    db_session.add(check)
    db_session.flush()
    return check


@pytest.fixture
def world(db_session: Session, captured: Connection) -> dict[str, Any]:  # noqa: F811
    me = _user(db_session)
    return {
        "me": me,
        "conn": captured,
        "raw": _asset_id(db_session, "raw", "feedback"),
        "silver": _asset_id(db_session, "silver", "feedback"),
        "gold": _asset_id(db_session, "gold", "feedback_sentiment"),
    }


def _advice(db_session: Session, w: dict[str, Any], column: str, etype: str = UNIQUE) -> Any:
    return lp.placement_for(
        db_session, asset_id=w["gold"], column=column, expectation_type=etype, user_id=w["me"].id
    )


def test_pass_through_column_with_no_upstream_check_is_placed_at_its_origin(
    db_session: Session, world: dict[str, Any]
) -> None:
    out = _advice(db_session, world, "customer_id")
    assert out["recommendation"] == lp.PLACE_AT_ORIGIN
    assert out["complete"] is True
    (origin,) = out["origins"]
    assert origin["asset_name"] == "dataq_retail.raw.feedback"
    assert origin["column"] == "customer_id"
    assert origin["pass_through"] is True and origin["confirmed"] is True
    assert origin["has_equivalent_check"] is False
    assert out["equivalent_upstream_checks"] == []


def test_equivalent_check_upstream_is_named_when_visible(
    db_session: Session, world: dict[str, Any]
) -> None:
    upstream = _suite(db_session, world["conn"], world["me"], world["silver"])
    check = _check(db_session, upstream, UNIQUE, "CUSTOMER_ID")  # fold-matched on UC
    out = _advice(db_session, world, "customer_id")
    assert out["recommendation"] == lp.ALREADY_COVERED_UPSTREAM
    (eq,) = out["equivalent_upstream_checks"]
    assert eq["check_id"] == str(check.id)
    assert eq["asset_name"] == "dataq_retail.silver.feedback"
    assert out["restricted_equivalent_checks"] == 0


def test_equivalent_check_on_an_ungranted_suite_is_counted_not_named(
    db_session: Session, world: dict[str, Any]
) -> None:
    stranger = _user(db_session)
    upstream = _suite(db_session, world["conn"], stranger, world["raw"])
    _check(db_session, upstream, UNIQUE, "customer_id")
    out = _advice(db_session, world, "customer_id")
    assert out["recommendation"] == lp.ALREADY_COVERED_UPSTREAM
    assert out["equivalent_upstream_checks"] == []
    assert out["restricted_equivalent_checks"] == 1
    # …and once shared, it is named.
    db_session.add(Share(suite_id=upstream.id, user_id=world["me"].id, permission="view"))
    db_session.flush()
    shared = _advice(db_session, world, "customer_id")
    assert len(shared["equivalent_upstream_checks"]) == 1
    assert shared["restricted_equivalent_checks"] == 0


def test_a_different_check_type_upstream_is_not_an_equivalent(
    db_session: Session, world: dict[str, Any]
) -> None:
    upstream = _suite(db_session, world["conn"], world["me"], world["raw"])
    _check(db_session, upstream, NOT_NULL, "customer_id")
    out = _advice(db_session, world, "customer_id")
    assert out["recommendation"] == lp.PLACE_AT_ORIGIN
    assert out["equivalent_upstream_checks"] == []


def test_a_derived_column_gets_provenance_but_no_recommendation(
    db_session: Session, world: dict[str, Any]
) -> None:
    """`sentiment` derives from `comment`: a check on raw.feedback.comment is NOT equivalent."""
    upstream = _suite(db_session, world["conn"], world["me"], world["raw"])
    _check(db_session, upstream, UNIQUE, "comment")
    out = _advice(db_session, world, "sentiment")
    assert out["recommendation"] is None
    assert out["equivalent_upstream_checks"] == []
    (origin,) = out["origins"]
    assert origin["column"] == "comment" and origin["pass_through"] is False


def test_a_check_on_the_suggestions_own_asset_is_not_upstream(
    db_session: Session, world: dict[str, Any]
) -> None:
    own = _suite(db_session, world["conn"], world["me"], world["gold"])
    _check(db_session, own, UNIQUE, "customer_id")
    out = _advice(db_session, world, "customer_id")
    assert out["recommendation"] == lp.PLACE_AT_ORIGIN


def test_a_cycle_back_to_the_suggestions_own_asset_is_not_upstream_coverage(
    db_session: Session, world: dict[str, Any]
) -> None:
    """A.X → B.X → A.X: the walk comes back to the start; the start's own check is not an
    upstream equivalent."""
    from backend.app.db.models import LineageEdge
    from backend.app.services.asset_service import upsert_assets

    ns = "snowflake://ACCT"
    ids = upsert_assets(db_session, [{"namespace": ns, "name": n} for n in ("D.S.A", "D.S.B")])
    a, b = ids[(ns, "D.S.A")], ids[(ns, "D.S.B")]
    for up, down in ((a, b), (b, a)):
        db_session.add(
            LineageEdge(
                upstream_asset_id=up,
                downstream_asset_id=down,
                source="snowflake",
                connection_id=world["conn"].id,
                columns=[["X", "X"]],
                column_grain="captured",
            )
        )
    own = _suite(db_session, world["conn"], world["me"], a)
    _check(db_session, own, UNIQUE, "X")
    out = lp.placement_for(
        db_session, asset_id=a, column="X", expectation_type=UNIQUE, user_id=world["me"].id
    )
    assert out["equivalent_upstream_checks"] == []
    assert out["recommendation"] != lp.ALREADY_COVERED_UPSTREAM


def test_no_lineage_means_no_recommendation(db_session: Session, world: dict[str, Any]) -> None:
    out = lp.placement_for(
        db_session,
        asset_id=world["raw"],
        column="customer_id",
        expectation_type=UNIQUE,
        user_id=world["me"].id,
    )
    assert out["upstream_status"] == "no_table_lineage"
    assert out["recommendation"] is None and out["origins"] == []


def test_annotate_is_fail_soft_per_suggestion_and_keeps_the_session_usable(
    db_session: Session, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session, world["conn"], world["me"], world["gold"])
    real = lp.placement_for
    calls: list[str] = []

    def flaky(session: Session, **kw: Any) -> Any:
        calls.append(kw["column"])
        if kw["column"] == "boom":
            session.execute(text("SELECT 1 / 0"))  # a real Postgres error inside the savepoint
        return real(session, **kw)

    monkeypatch.setattr(lp, "placement_for", flaky)
    suggestions: list[dict[str, Any]] = [
        {"expectation_type": UNIQUE, "config": {"column": "boom"}},
        {"expectation_type": UNIQUE, "config": {"column": "customer_id"}},
        {"expectation_type": "monitor:volume", "config": {}},
    ]
    lp.annotate_suggestions(
        db_session, suite=suite, user_id=world["me"].id, suggestions=suggestions
    )
    assert suggestions[0]["lineage"] == {"upstream_status": "error"}
    assert suggestions[1]["lineage"]["recommendation"] == lp.PLACE_AT_ORIGIN
    assert "lineage" not in suggestions[2]
    assert calls == ["boom", "customer_id"]
    # The aborted statement was contained by the SAVEPOINT: the outer transaction still works.
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_annotate_skips_a_suite_with_no_asset(db_session: Session, world: dict[str, Any]) -> None:
    suite = Suite(name="x", connection_id=world["conn"].id, created_by=world["me"].id)
    db_session.add(suite)
    db_session.flush()
    suggestions: list[dict[str, Any]] = [{"expectation_type": UNIQUE, "config": {"column": "a"}}]
    lp.annotate_suggestions(
        db_session, suite=suite, user_id=world["me"].id, suggestions=suggestions
    )
    assert "lineage" not in suggestions[0]


def test_validate_output_attaches_the_advice(db_session: Session, world: dict[str, Any]) -> None:
    from backend.app.db.models import LlmInvocation
    from backend.app.services import llm_checksuggest

    suite = _suite(db_session, world["conn"], world["me"], world["gold"])
    invocation = LlmInvocation(
        kind=llm_checksuggest.CHECKSUGGEST_KIND,
        requested_by_user_id=world["me"].id,
        suite_id=suite.id,
        request={llm_checksuggest.COLUMNS_KEY: ["customer_id", "rating"]},
    )
    db_session.add(invocation)
    db_session.flush()
    out = llm_checksuggest.validate_output(
        db_session,
        invocation,
        {
            "suggestions": [
                {"expectation_type": UNIQUE, "name": "u", "config": {"column": "customer_id"}}
            ]
        },
    )
    (suggestion,) = out["suggestions"]
    assert suggestion["lineage"]["recommendation"] == lp.PLACE_AT_ORIGIN
