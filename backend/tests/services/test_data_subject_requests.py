"""Tests for the data-subject-rights machinery (G2, #432)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.db.models import (
    Check,
    CheckVersion,
    Connection,
    Incident,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.services import data_subject_requests as dsr
from backend.app.services import suite_service


def _suite_and_check(
    db_session: Any, *, config: dict[str, Any] | None = None
) -> tuple[Suite, Check]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config=config or {})
    db_session.add(check)
    db_session.flush()
    return suite, check


def _result(
    db_session: Any,
    check: Check,
    *,
    sample: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> Result:
    run = Run(suite_id=check.suite_id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    row = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        sample_failures=sample,
        observed_value=observed,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db_session.add(row)
    db_session.commit()
    return row


# ── sample_failures matching ─────────────────────────────────────────────────


def test_finds_match_in_unexpected_index_list(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session)
    result = _result(
        db_session,
        check,
        sample={
            "unexpected_index_list": [
                {"email": "alice@example.com", "id": 1},
                {"email": "bob@example.com", "id": 2},
            ]
        },
    )

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")

    assert [m.result_id for m in matched] == [result.id]
    assert matched[0].matched_in == ("sample_failures",)


def test_finds_match_in_comparison_bucket(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session)
    result = _result(
        db_session,
        check,
        sample={"mismatched": [{"email_src": "x@y.com", "email_tgt": "x@z.com"}]},
    )

    matched = dsr.find_matching_results(db_session, column="email_src", value="x@y.com")

    assert [m.result_id for m in matched] == [result.id]


def test_no_match_returns_empty(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session)
    _result(db_session, check, sample={"unexpected_index_list": [{"email": "nobody@x.com"}]})

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")

    assert matched == []


def test_erase_removes_only_the_matching_row_keeps_the_rest(db_session: Any) -> None:
    """Surgical erasure: only alice's row is dropped; bob's row and every other key
    in the sample survive untouched.
    """
    _suite, check = _suite_and_check(db_session)
    result = _result(
        db_session,
        check,
        sample={
            "unexpected_index_list": [
                {"email": "alice@example.com", "id": 1},
                {"email": "bob@example.com", "id": 2},
            ],
            "element_count": 500,
        },
    )

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()

    assert summary.erased_count == 1
    db_session.refresh(result)
    assert result.sample_failures == {
        "unexpected_index_list": [{"email": "bob@example.com", "id": 2}],
        "element_count": 500,  # untouched sibling key
    }


def test_erase_is_idempotent(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session)
    _result(
        db_session,
        check,
        sample={"unexpected_index_list": [{"email": "alice@example.com"}]},
    )

    first = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()
    second = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()

    assert first.erased_count == 1
    assert second.erased_count == 0


# ── observed_value matching (unparsed_value shape) ───────────────────────────


def test_finds_and_erases_unparsed_value_match(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session)
    result = _result(
        db_session,
        check,
        observed={"unparsed_value": "alice@example.com", "column": "email"},
    )

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")
    assert [m.result_id for m in matched] == [result.id]

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()
    assert summary.erased_count == 1
    db_session.refresh(result)
    # the column NAME survives — only the value is scrubbed
    assert result.observed_value == {"unparsed_value": None, "column": "email"}


def test_unparsed_value_wrong_column_name_does_not_match(db_session: Any) -> None:
    """Same value, different column: not a match — the (column, value) pair is
    the whole identity.
    """
    _suite, check = _suite_and_check(db_session)
    _result(
        db_session, check, observed={"unparsed_value": "alice@example.com", "column": "contact"}
    )

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")

    assert matched == []


# ── observed_value matching (list-shaped, #1229/#1252) ───────────────────────


def test_finds_and_erases_list_shaped_observed_value_match(db_session: Any) -> None:
    _suite, check = _suite_and_check(db_session, config={"column": "email"})
    result = _result(
        db_session,
        check,
        observed={"observed_value": ["alice@example.com", "bob@example.com"]},
    )

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")
    assert [m.result_id for m in matched] == [result.id]

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()
    assert summary.erased_count == 1
    db_session.refresh(result)
    # bob's value survives; only alice's is removed from the list
    assert result.observed_value == {"observed_value": ["bob@example.com"]}


def test_list_shaped_observed_value_ignored_when_check_tests_a_different_column(
    db_session: Any,
) -> None:
    """Without the check's tested column matching, a raw distinct-value list can't
    be attributed to any particular column — so it must not match.
    """
    _suite, check = _suite_and_check(db_session, config={"column": "created_at"})
    _result(db_session, check, observed={"observed_value": ["alice@example.com"]})

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")

    assert matched == []


def test_scalar_observed_value_never_matches(db_session: Any) -> None:
    """A scalar aggregate (row count, mean) sharing the observed_value wrapper must
    never be mistaken for a subject-bearing shape.
    """
    _suite, check = _suite_and_check(db_session, config={"column": "email"})
    _result(db_session, check, observed={"observed_value": 34680})

    matched = dsr.find_matching_results(db_session, column="email", value="34680")

    assert matched == []


# ── historical tested-column resolution ──────────────────────────────────────


def test_matches_by_the_column_in_effect_when_the_result_was_written(db_session: Any) -> None:
    """A check's `config.column` can change after a result was captured (#1489's
    motivating case). Matching must resolve the tested column AS OF the result's
    own `created_at` via `historical_check_context`, not the check's CURRENT
    config — otherwise a later edit silently makes a subject's already-captured
    data unreachable by both export and erase.
    """
    _suite, check = _suite_and_check(db_session, config={"column": "user_email"})
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    result = _result(
        db_session,
        check,
        observed={"observed_value": ["alice@example.com"]},
        created_at=t0,
    )
    # The check is edited AFTER the result was written — a new version records the
    # OLD column, and the check's live config (above) now points somewhere else.
    db_session.add(
        CheckVersion(
            check_id=check.id,
            version_no=1,
            name=check.name,
            kind=check.kind,
            expectation_type=check.expectation_type,
            config={"column": "email"},
            created_at=t0,
        )
    )
    db_session.add(
        CheckVersion(
            check_id=check.id,
            version_no=2,
            name=check.name,
            kind=check.kind,
            expectation_type=check.expectation_type,
            config={"column": "user_email"},
            created_at=t1,
        )
    )
    db_session.commit()

    matched = dsr.find_matching_results(db_session, column="email", value="alice@example.com")

    assert [m.result_id for m in matched] == [result.id]

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()
    assert summary.erased_count == 1
    db_session.refresh(result)
    assert result.observed_value == {"observed_value": []}


# ── incidents.evidence (#1795) ───────────────────────────────────────────────


def _incident(
    db_session: Any, *, column: str, observed: dict[str, Any] | None
) -> tuple[Incident, Check]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a", "database": "RETAIL", "schema": "PUBLIC", "warehouse": "WH"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = suite_service.create_suite(
        db_session,
        name=f"suite-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    check = Check(
        suite_id=suite.id, name="c", expectation_type="expect_x", config={"column": column}
    )
    db_session.add(check)
    db_session.flush()
    incident = Incident(
        asset_id=suite.asset_id,
        check_id=check.id,
        suite_id=suite.id,
        status="open",
        evidence={
            "failing_result": {
                "status": "fail",
                "metric_value": None,
                "observed_value": observed,
                "expected_value": None,
            },
            "same_asset_siblings": [],
        },
    )
    db_session.add(incident)
    db_session.commit()
    return incident, check


def test_finds_a_subject_in_an_incident_evidence_snapshot(db_session: Any) -> None:
    incident, check = _incident(
        db_session, column="email", observed={"observed_value": ["alice@example.com", "x@y.z"]}
    )

    matched = dsr.find_matching_incidents(db_session, column="email", value="alice@example.com")

    assert [m.incident_id for m in matched] == [incident.id]
    assert matched[0].check_id == check.id and matched[0].tested_column == "email"
    assert dsr.find_matching_incidents(db_session, column="email", value="nobody@x.y") == []


def test_erase_scrubs_the_incident_snapshot_in_place_and_reports_it_separately(
    db_session: Any,
) -> None:
    incident, _check = _incident(
        db_session, column="email", observed={"observed_value": ["alice@example.com", "x@y.z"]}
    )

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()

    assert summary.matched_result_ids == [] and summary.erased_count == 0
    assert summary.matched_incident_ids == [incident.id]
    assert summary.erased_incident_count == 1
    db_session.refresh(incident)
    assert incident.evidence is not None
    assert incident.evidence["failing_result"]["observed_value"] == {"observed_value": ["x@y.z"]}
    assert incident.evidence["failing_result"]["status"] == "fail"  # the rest of the card survives
    assert incident.evidence["same_asset_siblings"] == []
    # Idempotent: nothing left to match, nothing rewritten.
    again = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    assert again.matched_incident_ids == [] and again.erased_incident_count == 0


def test_incident_unparsed_value_cell_is_matched_and_scrubbed(db_session: Any) -> None:
    incident, _check = _incident(
        db_session,
        column="email",
        observed={"column": "email", "unparsed_value": "alice@example.com"},
    )

    summary = dsr.erase_matching_results(db_session, column="email", value="alice@example.com")
    db_session.commit()

    assert summary.erased_incident_count == 1
    db_session.refresh(incident)
    assert incident.evidence is not None
    assert incident.evidence["failing_result"]["observed_value"] == {
        "column": "email",
        "unparsed_value": None,
    }


def test_incident_scalar_or_redacted_snapshot_never_matches(db_session: Any) -> None:
    # Same rule as results: a scalar is not attributable to a subject; a masked
    # snapshot has nothing left to erase.
    _incident(db_session, column="email", observed={"observed_value": 34680})
    _incident(db_session, column="email", observed={"observed_value": "<redacted>"})

    assert dsr.find_matching_incidents(db_session, column="email", value="34680") == []
    assert dsr.find_matching_incidents(db_session, column="email", value="<redacted>") == []
