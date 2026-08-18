"""A warehouse tag actually masks, end to end — G3 / #433.

`test_column_tags.py` pins the fetcher's semantics and
`test_redaction_precedence.py` pins the ladder. Neither proves the two are
*connected* — that a tag cached on an asset reaches the redactor through the read
path a user actually hits. That connection is the whole feature, and it is three
hops (asset → suite → result), any of which could quietly drop the map.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Asset, Check, Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    """Clear FastAPI dependency overrides after EVERY test in this module.

    `_seed` sets `get_current_user` so the seeded owner is the caller, and three
    tests here take `db_session` without the `client` fixture that used to do the
    clearing — so the override leaked into later modules, where a subsequent test
    authenticated as a `User` whose row had been rolled back and hit a
    `ForeignKeyViolation` on `suites.created_by`.

    Autouse rather than a rule about which fixtures to request: a helper that
    mutates global state has to be paired with cleanup that runs regardless of
    what the test remembered to ask for.
    """
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db_session: Any, *, tags: dict[str, str] | None, column: str = "vendor_name") -> Run:
    """A run whose failing sample carries `column`, on an asset holding `tags`.

    `vendor_name` is chosen because the name heuristic clears it (an entity-
    qualified name, #1182) — so without a tag it surfaces, and any masking in
    these tests is attributable to the tag rather than to the classifier.
    """
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    asset = Asset(
        namespace=f"snowflake://acct-{uuid.uuid4().hex[:6]}",
        name="RETAIL.ORDERS",
        env="dev",
        column_tags=tags,
    )
    db_session.add(asset)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=owner.id,
        asset_id=asset.id,
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="values",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": column},
    )
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={
                "partial_unexpected_list": ["ACME LTD", "GLOBEX"],
                "unexpected_count": 2,
            },
        )
    )
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    return run


def _sample(client: TestClient, run: Run) -> Any:
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def test_without_a_tag_the_value_surfaces(client: TestClient, db_session: Any) -> None:
    """The precondition every other test here depends on.

    Without it, a masking assertion below would pass against a value the
    classifier was masking anyway, and the tag would be doing nothing.
    """
    run = _seed(db_session, tags=None)
    body = _sample(client, run)
    assert "ACME LTD" in str(body["sample_failures"])


def test_a_sensitive_warehouse_tag_masks_the_tested_column(
    client: TestClient, db_session: Any
) -> None:
    """The feature, end to end: the customer's own governance decides the masking.

    The tested column is the one shown *by design* — seeing the failing value is
    the point of a sample — so a tag that can mask it is a tag with real
    authority, not a hint.
    """
    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    body = _sample(client, run)
    assert "ACME LTD" not in str(body["sample_failures"])
    assert body["redaction"] in {"full", "partial"}


def test_a_tag_masks_a_sample_captured_BEFORE_it_was_applied(
    client: TestClient, db_session: Any
) -> None:
    """The reason the map lives on the ASSET rather than on each result.

    A classification is a statement about the data, not about the moment it was
    read — so tagging a column today must mask samples captured yesterday. Had
    the map been frozen onto each result at write time, this read would still
    expose the value, and the governance action would have silently applied only
    to future runs.
    """
    run = _seed(db_session, tags=None)
    assert "ACME LTD" in str(_sample(client, run)["sample_failures"]), "precondition"

    suite = db_session.get(Suite, run.suite_id)
    asset = db_session.get(Asset, suite.asset_id)
    asset.column_tags = {"vendor_name": "sensitive"}
    db_session.commit()

    assert "ACME LTD" not in str(_sample(client, run)["sample_failures"])


def test_a_suite_policy_cannot_lift_a_warehouse_tag(client: TestClient, db_session: Any) -> None:
    """The floor, asserted through the API rather than at the unit level.

    A tag is set by whoever governs the warehouse; naming the column as the
    suite's row locator is an application-level override. If the override won,
    the tag would be a suggestion — and the whole point of consuming warehouse
    classification is that it is not.
    """
    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    suite = db_session.get(Suite, run.suite_id)
    suite.column_policy = {"identifier_column": "vendor_name", "pii_columns": []}
    db_session.commit()

    assert "ACME LTD" not in str(_sample(client, run)["sample_failures"])


def test_a_suite_with_no_asset_reads_as_no_opinion(client: TestClient, db_session: Any) -> None:
    """The degradation path, and it must be the pre-G3 behaviour rather than a
    clearance or a crash.

    A suite with no resolved asset — targetless, or an unresolvable target — has
    nowhere to read tags from. That is the same state as a warehouse whose tags we
    cannot read, which is the state most deployments are in on day one.
    """
    run = _seed(db_session, tags=None)
    suite = db_session.get(Suite, run.suite_id)
    suite.asset_id = None
    db_session.commit()

    body = _sample(client, run)
    assert "ACME LTD" in str(body["sample_failures"]), "no tags → the ladder falls through"


def test_the_mcp_read_path_honours_the_same_tag(client: TestClient, db_session: Any) -> None:
    """The sibling door. `/mcp` serves the same samples to AI clients, and a floor
    applied at one door and not the other is the shape this repo keeps
    rediscovering — here it would mean the governance tag masks in the UI and not
    in the surface that carries values furthest.
    """
    from backend.app.mcp import server as mcp_server

    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    suite = db_session.get(Suite, run.suite_id)
    payload = mcp_server._run_results_payload(db_session, suite, run)
    assert "ACME LTD" not in str(payload["checks"])


# ── Review findings (PR #1468) ────────────────────────────────────────────────


def test_a_failed_tag_read_does_not_erase_the_cached_map(db_session: Any, monkeypatch: Any) -> None:
    """The worst of the review findings, and it inverted the safety property the
    module's own docstring claimed.

    A failed fetch used to return `{}`, which the refresh wrote straight over the
    cached map — so a permission error on the tag would **un-mask columns that
    were masked a minute earlier**. "I could not look" and "I looked and there is
    nothing" have to be different answers, because only one of them is safe to
    write down.
    """
    from backend.app.services import column_tags as ct

    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    suite = db_session.get(Suite, run.suite_id)
    asset = db_session.get(Asset, suite.asset_id)
    connection = db_session.get(Connection, suite.connection_id)
    # Past the TTL, so the refresh actually attempts a read.
    asset.column_tags_refreshed_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    monkeypatch.setattr(ct, "fetch_column_tags", lambda *a, **k: None)

    class _Target:
        table = "ORDERS"
        schema = "RETAIL"
        catalog = None

    ct.refresh_asset_column_tags(
        db_session,
        suite=suite,
        connection=connection,
        target=_Target(),
        secret_store=object(),  # type: ignore[arg-type]
    )

    db_session.expire_all()
    assert db_session.get(Asset, asset.id).column_tags == {"vendor_name": "sensitive"}


def test_a_successful_empty_read_does_clear_the_map(db_session: Any, monkeypatch: Any) -> None:
    """The other half — otherwise "never erase" becomes "never update".

    Un-tagging a column in the warehouse is a real governance action, and it has
    to take effect. This is the case that distinguishes a cautious cache from a
    write-once one.
    """
    from backend.app.services import column_tags as ct

    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    suite = db_session.get(Suite, run.suite_id)
    asset = db_session.get(Asset, suite.asset_id)
    connection = db_session.get(Connection, suite.connection_id)
    asset.column_tags_refreshed_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    monkeypatch.setattr(ct, "fetch_column_tags", lambda *a, **k: {})

    class _Target:
        table = "ORDERS"
        schema = "RETAIL"
        catalog = None

    ct.refresh_asset_column_tags(
        db_session,
        suite=suite,
        connection=connection,
        target=_Target(),
        secret_store=object(),  # type: ignore[arg-type]
    )

    db_session.expire_all()
    assert db_session.get(Asset, asset.id).column_tags == {}


def test_a_retargeted_suite_does_not_re_redact_old_runs_against_the_new_table(
    client: TestClient, db_session: Any
) -> None:
    """Tags are anchored on the RUN's asset, not the suite's current one.

    A suite can be retargeted at a different table; the runs it already produced
    are samples of the OLD one. Redacting them against the new table's
    classifications applies the wrong governance to the wrong data — and in the
    direction this test exercises, it would expose values that the table they
    actually came from had classified.
    """
    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    suite = db_session.get(Suite, run.suite_id)
    run_row = db_session.get(Run, run.id)
    run_row.asset_id = suite.asset_id  # what this run actually read

    # The suite is now pointed at a different table, whose columns are untagged.
    other = Asset(
        namespace=f"snowflake://acct-{uuid.uuid4().hex[:6]}",
        name="RETAIL.INVOICES",
        env="dev",
        column_tags={},
    )
    db_session.add(other)
    db_session.flush()
    suite.asset_id = other.id
    db_session.commit()

    body = _sample(client, run)
    assert "ACME LTD" not in str(body["sample_failures"]), (
        "the old run's sample must stay masked by the classification of the table "
        "it was actually read from"
    )


def test_alert_delivery_honours_the_tag_floor(db_session: Any) -> None:
    """The sibling door that leaves the platform.

    An alert goes to a webhook or a mailbox whose location DataQ does not know, so
    a governance floor honoured in the UI and not in the outbound message is
    honoured in the place that matters least.
    """
    from backend.app.alerting.builder import build_run_report

    run = _seed(db_session, tags={"vendor_name": "sensitive"})
    run_row = db_session.get(Run, run.id)
    suite = db_session.get(Suite, run.suite_id)
    run_row.asset_id = suite.asset_id
    db_session.commit()

    report = build_run_report(db_session, run_row)
    assert "ACME LTD" not in str([c.sample_summary for c in report.checks])
