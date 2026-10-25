"""contract_service + the ODCS export against a real Postgres (ADR 0052)."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.contracts import odcs
from backend.app.contracts import translate as tr
from backend.app.contracts.export import export_contract
from backend.app.db.models import (
    AuditEvent,
    Check,
    CheckVersion,
    Connection,
    Contract,
    MonitorBaseline,
    Result,
    Run,
    Schedule,
    Share,
    Suite,
    User,
)
from backend.app.services import contract_service as svc
from backend.app.services.schema_drift import diff_schemas


def _user(db: Any, role: str = "member") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex", role=role)
    db.add(u)
    db.flush()
    return u


def _connection(db: Any, owner: User, type_: str = "snowflake") -> Connection:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type=type_,
        env="dev",
        config={"account": "ab12345", "database": "ANALYTICS", "warehouse": "WH"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    return conn


def _doc(version: str = "1.0.0", **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": "seller-payments",
        "name": "seller_payments",
        "version": version,
        "status": "active",
        "domain": "seller",
        "schema": [
            {
                "name": "orders",
                "physicalName": "FINANCE.ORDERS",
                "properties": [
                    {
                        "name": "order_id",
                        "logicalType": "integer",
                        "required": True,
                        "unique": True,
                    },
                    {"name": "email", "logicalType": "string", "classification": "confidential"},
                    {
                        "name": "amount",
                        "logicalType": "number",
                        "quality": [
                            {"id": "amount-nulls", "metric": "nullValues", "mustBe": 0},
                        ],
                    },
                ],
                "quality": [{"id": "row-floor", "metric": "rowCount", "mustBeGreaterThan": 10}],
                "customProperties": [{"property": "dataq.schedule", "value": "0 2 * * *"}],
            },
            {"name": "refunds", "physicalName": "FINANCE.REFUNDS", "properties": [{"name": "id"}]},
        ],
        "slaProperties": [{"property": "retention", "value": 7, "unit": "y"}],
    }
    doc.update(overrides)
    return doc


def _import(
    db: Any, actor: User, conn: Connection, doc: dict[str, Any], **kw: Any
) -> svc.ImportOutcome:
    return svc.import_contract(
        db, document=doc, document_yaml=None, connection_id=conn.id, actor_id=actor.id, **kw
    )


def _suite(db: Any, outcome: svc.ImportOutcome, obj: str) -> Suite:
    suite: Suite | None = db.scalar(
        select(Suite).where(Suite.contract_id == outcome.contract.id, Suite.contract_object == obj)
    )
    assert suite is not None
    return suite


# ───────────────────────────── import ───────────────────────────────


def test_a_fresh_import_creates_one_suite_per_schema_object(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())

    contract = outcome.contract
    assert (contract.odcs_id, contract.version, contract.name, contract.domain) == (
        "seller-payments",
        "1.0.0",
        "seller_payments",
        "seller",
    )
    assert contract.spec["schema"][0]["name"] == "orders"  # stored verbatim
    assert len(outcome.created_suite_ids) == 2 and outcome.untargeted_objects == []

    orders = _suite(db_session, outcome, "orders")
    assert orders.target == {"schema": "FINANCE", "table": "ORDERS"}
    assert orders.column_policy == {"pii_columns": ["email"]}
    kinds = sorted(c.expectation_type for c in orders.checks)
    assert kinds == sorted(
        [
            "expect_column_values_to_not_be_null",  # order_id required
            "expect_column_values_to_be_unique",
            "expect_column_values_to_not_be_null",  # amount nulls rule
            "monitor:schema_drift",
            "monitor:volume",
        ]
    )
    assert all(c.origin == "contract" and c.contract_ref for c in orders.checks)
    # Every created check has its v1 snapshot.
    versions = db_session.scalars(
        select(CheckVersion).where(CheckVersion.check_id.in_([c.id for c in orders.checks]))
    ).all()
    assert len(versions) == len(orders.checks)
    # The schedule from customProperties.
    schedule = db_session.scalar(select(Schedule).where(Schedule.suite_id == orders.id))
    assert schedule is not None and schedule.cron == "0 2 * * *"
    # The declared schema-drift baseline is seeded, not learned.
    drift = next(c for c in orders.checks if c.expectation_type == "monitor:schema_drift")
    baseline = db_session.scalar(
        select(MonitorBaseline).where(MonitorBaseline.check_id == drift.id)
    )
    assert baseline is not None and baseline.baseline["declared"] is True
    assert [c["name"] for c in baseline.baseline["columns"]] == ["order_id", "email", "amount"]
    # Not-enforced clauses are stored, with the reason.
    assert [u["field"] for u in contract.unmapped] == ["slaProperties.retention"]


def test_the_import_is_audited_with_counts_never_the_document(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "contract.import", AuditEvent.entity_id == outcome.contract.id
        )
    )
    assert event is not None
    assert event.after["suites_created"] == 2 and event.after["unmapped"] == 1
    assert "FINANCE.ORDERS" not in json.dumps(event.after)
    assert "schema" not in event.after


def test_an_unresolvable_physical_name_leaves_the_suite_untargeted_and_says_so(
    db_session: Any,
) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    doc = _doc()
    doc["schema"][1]["physicalName"] = "a.b.c.d"
    outcome = _import(db_session, actor, conn, doc)
    assert outcome.untargeted_objects == ["refunds"]
    assert _suite(db_session, outcome, "refunds").target is None


def test_a_check_the_validators_refuse_becomes_an_unmapped_clause(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    doc = _doc()
    # A negative row ceiling is a volume config the save-time validator refuses.
    doc["schema"][0]["quality"] = [{"id": "bad", "metric": "rowCount", "mustBeLessThan": 0}]
    outcome = _import(db_session, actor, conn, doc)
    reasons = [u for u in outcome.contract.unmapped if u.get("rule_id") == "bad"]
    assert len(reasons) == 1 and reasons[0]["reason"].startswith("DataQ refused")
    orders = _suite(db_session, outcome, "orders")
    assert "monitor:volume" not in {c.expectation_type for c in orders.checks}


def test_the_same_version_is_refused_unless_replace(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    first = _import(db_session, actor, conn, _doc())
    with pytest.raises(svc.ContractVersionExistsError):
        _import(db_session, actor, conn, _doc())
    again = _import(db_session, actor, conn, _doc(), replace=True)
    assert again.contract.id == first.contract.id
    assert again.created_suite_ids == [] and len(again.reconciled_suite_ids) == 2
    assert (again.checks_added, again.checks_updated, again.checks_disabled) == (0, 0, 0)


def test_a_new_version_reconciles_the_suites_it_takes_over(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    first = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, first, "orders")
    by_rule = {(c.contract_ref or {}).get("rule_id"): c for c in orders.checks}

    # A person edits the row-floor check; and adds their own check to the suite.
    edited = by_rule["row-floor"]
    edited.config = {"min_rows": 50}
    hand = Check(
        suite_id=orders.id,
        name="mine",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "email"},
        ordinal=99,
    )
    db_session.add(hand)
    db_session.commit()

    v2 = copy.deepcopy(_doc("2.0.0"))
    v2["schema"][0]["quality"] = [
        {"id": "row-floor", "metric": "rowCount", "mustBeGreaterThan": 99}
    ]
    v2["schema"][0]["properties"][2]["quality"] = [
        {"id": "amount-nulls", "metric": "nullValues", "mustBeLessThan": 5, "unit": "percent"}
    ]
    v2["schema"][0]["properties"].append({"name": "currency", "required": True})
    del v2["schema"][0]["properties"][0]["unique"]  # the unique clause disappears

    suite_ids = list(first.created_suite_ids)
    second = _import(db_session, actor, conn, v2, suite_ids=suite_ids)
    assert second.contract.id != first.contract.id
    assert sorted(second.reconciled_suite_ids) == sorted(suite_ids)
    db_session.refresh(orders)
    checks = {c.id: c for c in orders.checks}

    assert checks[edited.id].config == {"min_rows": 50}  # a person's edit survives
    assert checks[hand.id].enabled and checks[hand.id].origin == "user"  # untouched
    nulls = next(
        c for c in checks.values() if (c.contract_ref or {}).get("rule_id") == "amount-nulls"
    )
    assert nulls.fail_threshold == 5  # unedited → updated to the new clause
    unique = next(
        c for c in checks.values() if c.expectation_type == "expect_column_values_to_be_unique"
    )
    assert unique.enabled is False  # disabled, never deleted
    assert any(
        (c.contract_ref or {}).get("property") == "currency" for c in checks.values()
    )  # added
    assert orders.contract_id == second.contract.id
    # Updated: the nulls rule, and the declared schema baseline (it now lists `currency`).
    assert (second.checks_added, second.checks_updated, second.checks_disabled) == (1, 2, 1)
    drift = next(c for c in checks.values() if c.expectation_type == "monitor:schema_drift")
    baseline = db_session.scalar(
        select(MonitorBaseline).where(MonitorBaseline.check_id == drift.id)
    )
    assert baseline is not None
    assert "currency" in [c["name"] for c in baseline.baseline["columns"]]


def test_a_suite_from_another_contract_cannot_be_taken_over(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    other = _import(db_session, actor, conn, _doc(id="other-contract"))
    with pytest.raises(svc.ContractSuitesInvalidError):
        _import(db_session, actor, conn, _doc("2.0.0"), suite_ids=other.created_suite_ids[:1])


# ───────────────────────────── reads ────────────────────────────────


def test_visibility_follows_the_linked_suites(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    outsider = _user(db_session)
    viewer = _user(db_session)
    db_session.add(
        Share(suite_id=outcome.created_suite_ids[0], user_id=viewer.id, permission="view")
    )
    db_session.commit()

    assert [v.contract.id for v in svc.list_contracts(db_session, owner.id)] == [
        outcome.contract.id
    ]
    assert svc.list_contracts(db_session, outsider.id) == []
    with pytest.raises(svc.ContractNotFoundError):
        svc.get_contract(db_session, outcome.contract.id, outsider.id)
    seen = svc.get_contract(db_session, outcome.contract.id, viewer.id)
    assert len(seen.suites) == 1 and seen.restricted_suite_count == 1


def test_an_admin_sees_a_contract_with_no_linked_suite(db_session: Any) -> None:
    owner = _user(db_session)
    admin = _user(db_session, role="admin")
    contract = Contract(
        odcs_id="lonely",
        name="lonely",
        version="1",
        api_version="v3.2.0",
        spec={},
        created_by=owner.id,
    )
    db_session.add(contract)
    db_session.commit()
    assert [v.contract.id for v in svc.list_contracts(db_session, admin.id)] == [contract.id]
    assert svc.list_contracts(db_session, owner.id) == []


def test_delete_unlinks_both_columns_and_keeps_the_suites(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    svc.delete_contract(db_session, outcome.contract.id, actor.id)
    for suite_id in outcome.created_suite_ids:
        suite = db_session.get(Suite, suite_id)
        assert suite is not None and (suite.contract_id, suite.contract_object) == (None, None)
        assert suite.checks  # checks stay


def test_delete_needs_edit_on_every_linked_suite(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    viewer = _user(db_session)
    db_session.add(
        Share(suite_id=outcome.created_suite_ids[0], user_id=viewer.id, permission="view")
    )
    db_session.commit()
    with pytest.raises(svc.ContractForbiddenError) as excinfo:
        svc.delete_contract(db_session, outcome.contract.id, viewer.id)
    # The hidden suite is never named (ADR 0037).
    detail = json.dumps(excinfo.value.detail or {})
    assert str(outcome.created_suite_ids[1]) not in detail


# ───────────────────────────── export ───────────────────────────────


def test_export_validates_and_round_trips_every_check(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, outcome, "orders")
    db_session.add(
        Check(
            suite_id=orders.id,
            name="a person's anomaly-free check",
            kind="expectation",
            expectation_type="expect_column_values_to_be_in_set",
            config={"column": "currency", "value_set": ["EUR", "USD"]},
            ordinal=50,
        )
    )
    db_session.commit()
    db_session.refresh(orders)

    visible = svc.get_contract(db_session, outcome.contract.id, actor.id)
    exported = export_contract(db_session, visible.contract, visible.suites)
    assert exported["apiVersion"] == odcs.NEWEST_VERSION
    odcs.validate_document(exported)  # a valid ODCS document

    # Importing the export recreates every check the suite holds, the hand-authored one included.
    translated = tr.translate(exported, connection_type="snowflake")
    obj = next(o for o in translated.objects if o.name == "orders")
    assert sorted(d.expectation_type for d in obj.checks) == sorted(
        c.expectation_type for c in orders.checks
    )
    # A contract-derived rule carries its check id.
    floor = next(r for r in exported["schema"][0]["quality"] if r.get("id") == "row-floor")
    assert {
        "property": "dataq.checkId",
        "value": str(
            next(
                c.id for c in orders.checks if (c.contract_ref or {}).get("rule_id") == "row-floor"
            )
        ),
    } in floor["customProperties"]
    # The stored spec is untouched by the export.
    assert "customProperties" not in visible.contract.spec["schema"][1]


# ───────────────────────── declared baseline diff ─────────────────────────


def test_a_declared_baseline_matches_names_case_insensitively_and_skips_untyped_columns() -> None:
    declared: list[dict[str, Any]] = [
        {"name": "order_id", "type": "NUMBER"},
        {"name": "email", "type": None},
    ]
    current: list[dict[str, Any]] = [
        {"name": "ORDER_ID", "type": "NUMBER"},
        {"name": "EMAIL", "type": "VARCHAR"},
    ]
    assert diff_schemas(declared, current, declared=True) == {
        "added": [],
        "removed": [],
        "type_changed": [],
        "columns_checked": 2,
    }
    # A learned baseline keeps exact names.
    learned = diff_schemas(declared, current)
    assert learned["added"] == ["EMAIL", "ORDER_ID"] and learned["removed"] == ["email", "order_id"]


def test_a_declared_type_mismatch_is_still_reported() -> None:
    declared: list[dict[str, Any]] = [{"name": "order_id", "type": "NUMBER"}]
    current: list[dict[str, Any]] = [{"name": "ORDER_ID", "type": "VARCHAR"}]
    assert diff_schemas(declared, current, declared=True)["type_changed"] == [
        {"column": "ORDER_ID", "from": "NUMBER", "to": "VARCHAR"}
    ]


def test_replace_needs_the_contract_visible_and_edit_on_every_linked_suite(
    db_session: Any,
) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    stranger = _user(db_session)
    with pytest.raises(svc.ContractVersionExistsError) as excinfo:
        _import(db_session, stranger, conn, _doc(), replace=True)
    assert "contract_id" not in (excinfo.value.detail or {})  # existence, not identity
    viewer = _user(db_session)
    db_session.add(
        Share(suite_id=outcome.created_suite_ids[0], user_id=viewer.id, permission="view")
    )
    db_session.commit()
    with pytest.raises(svc.ContractForbiddenError):
        _import(db_session, viewer, conn, _doc(), replace=True)


def test_a_suite_on_another_connection_is_not_reconciled(db_session: Any) -> None:
    actor = _user(db_session)
    first_conn = _connection(db_session, actor)
    other_conn = _connection(db_session, actor)
    first = _import(db_session, actor, first_conn, _doc())
    with pytest.raises(svc.ContractSuitesInvalidError, match="different connection"):
        _import(db_session, actor, other_conn, _doc("2.0.0"), suite_ids=first.created_suite_ids)


def test_a_view_share_on_every_suite_still_cannot_delete(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    viewer = _user(db_session)
    for suite_id in outcome.created_suite_ids:
        db_session.add(Share(suite_id=suite_id, user_id=viewer.id, permission="view"))
    db_session.commit()
    with pytest.raises(Exception, match="permission"):
        svc.delete_contract(db_session, outcome.contract.id, viewer.id)


def _rule_check(suite: Suite, rule_id: str) -> Check:
    return next(c for c in suite.checks if (c.contract_ref or {}).get("rule_id") == rule_id)


def test_a_clause_that_returns_re_enables_only_a_check_the_contract_switched_off(
    db_session: Any,
) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    v1 = _import(db_session, actor, conn, _doc())
    ids = list(v1.created_suite_ids)
    orders = _suite(db_session, v1, "orders")
    floor_id = _rule_check(orders, "row-floor").id

    dropped = _doc("2.0.0")
    dropped["schema"][0]["quality"] = []
    _import(db_session, actor, conn, dropped, suite_ids=ids)
    db_session.refresh(orders)
    assert db_session.get(Check, floor_id).enabled is False

    _import(db_session, actor, conn, _doc("3.0.0"), suite_ids=ids)
    db_session.refresh(orders)
    floor = db_session.get(Check, floor_id)
    assert floor is not None and floor.enabled is True
    assert svc.DISABLED_BY_CONTRACT not in (floor.contract_ref or {})


def test_a_check_a_person_switched_off_stays_off_when_its_clause_changes(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    v1 = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, v1, "orders")
    nulls = _rule_check(orders, "amount-nulls")
    nulls.enabled = False
    db_session.commit()

    v2 = _doc("2.0.0")
    v2["schema"][0]["properties"][2]["quality"] = [
        {"id": "amount-nulls", "metric": "nullValues", "mustBeLessThan": 5, "unit": "percent"}
    ]
    _import(db_session, actor, conn, v2, suite_ids=list(v1.created_suite_ids))
    refreshed = db_session.get(Check, nulls.id)
    assert refreshed is not None
    assert refreshed.fail_threshold == 5  # the clause change applied
    assert refreshed.enabled is False  # the person's off-switch held


def test_a_new_version_that_drops_an_object_retires_its_suite(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    v1 = _import(db_session, actor, conn, _doc())
    refunds = _suite(db_session, v1, "refunds")
    v2 = _doc("2.0.0")
    v2["schema"] = v2["schema"][:1]
    outcome = _import(db_session, actor, conn, v2, suite_ids=list(v1.created_suite_ids))
    assert outcome.retired_suite_ids == [refunds.id]
    db_session.refresh(refunds)
    assert (refunds.contract_id, refunds.contract_object) == (None, None)
    assert all(not c.enabled for c in refunds.checks if c.origin == "contract")


def test_two_suites_for_one_object_cannot_both_be_taken_over(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    first = _import(db_session, actor, conn, _doc())
    again = _import(db_session, actor, conn, _doc("1.1.0"))  # fresh suites, same objects
    orders_ids = [_suite(db_session, first, "orders").id, _suite(db_session, again, "orders").id]
    with pytest.raises(svc.ContractSuitesInvalidError, match="same schema object"):
        _import(db_session, actor, conn, _doc("2.0.0"), suite_ids=orders_ids)


def test_export_finds_a_rule_on_a_property_with_its_own_physical_name(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    doc = _doc()
    doc["schema"][0]["properties"][2]["physicalName"] = "AMOUNT_EUR"
    outcome = _import(db_session, actor, conn, doc)
    visible = svc.get_contract(db_session, outcome.contract.id, actor.id)
    exported = export_contract(db_session, visible.contract, visible.suites)
    amount = exported["schema"][0]["properties"][2]
    assert any(
        p.get("property") == "dataq.checkId" for p in amount["quality"][0]["customProperties"]
    )
    # Not appended a second time as a dataq-engine rule.
    assert not [r for r in exported["schema"][0].get("quality", []) if r.get("engine") == "dataq"]


def test_id_less_rules_keep_their_checks_when_a_rule_is_inserted_before_them(
    db_session: Any,
) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    v1 = _doc()
    v1["schema"][0]["quality"] = [{"metric": "rowCount", "mustBeGreaterThan": 10}]
    first = _import(db_session, actor, conn, v1)
    orders = _suite(db_session, first, "orders")
    volume = next(c for c in orders.checks if c.expectation_type == "monitor:volume")
    v2 = _doc("2.0.0")
    v2["schema"][0]["quality"] = [
        {"type": "sql", "query": "SELECT COUNT(*) FROM {object}", "mustBeGreaterThan": 0},
        {"metric": "rowCount", "mustBeGreaterThan": 10},
    ]
    second = _import(db_session, actor, conn, v2, suite_ids=list(first.created_suite_ids))
    kept = db_session.get(Check, volume.id)
    assert kept is not None
    # Still the volume check for its own rule — a positional key would rewrite it into the SQL
    # rule that now sits first.
    assert (kept.expectation_type, kept.config, kept.enabled) == (
        "monitor:volume",
        {"min_rows": 11},
        True,
    )
    assert (second.checks_added, second.checks_updated, second.checks_disabled) == (1, 0, 0)


def test_a_learned_baseline_still_reports_a_type_appearing_where_none_was_recorded() -> None:
    """#2550 review: the untyped-column skip is for DECLARED baselines only."""
    learned: list[dict[str, Any]] = [{"name": "a", "type": None}]
    current: list[dict[str, Any]] = [{"name": "a", "type": "INT"}]
    assert diff_schemas(learned, current)["type_changed"] == [
        {"column": "a", "from": None, "to": "INT"}
    ]


# ───────────────────────────── compliance (phase B) ─────────────────────────────


def _run_with(db: Any, suite: Suite, status: str, results: dict[uuid.UUID, str]) -> Run:
    run = Run(suite_id=suite.id, status=status)
    db.add(run)
    db.flush()
    for check_id, result_status in results.items():
        db.add(Result(run_id=run.id, check_id=check_id, status=result_status))
    db.commit()
    return run


def test_compliance_reports_each_clause_from_the_latest_run(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, outcome, "orders")
    floor = _rule_check(orders, "row-floor")
    nulls = _rule_check(orders, "amount-nulls")
    _run_with(db_session, orders, "succeeded", {floor.id: "pass", nulls.id: "fail"})
    # An older run never counts: only the newest per suite.
    visible = svc.get_contract(db_session, outcome.contract.id, actor.id)
    result = svc.compliance(db_session, visible)

    states = {(c.clause["rule_id"], c.state) for c in result.clauses if c.clause["rule_id"]}
    assert ("row-floor", "pass") in states and ("amount-nulls", "fail") in states
    orders_suite = next(x for x in result.suites if x.suite_id == orders.id)
    assert orders_suite.results_final is True
    refunds = next(x for x in result.suites if x.suite_id != orders.id)
    assert refunds.last_run_id is None and refunds.results_final is False
    assert result.counts["pass"] == 1 and result.counts["fail"] == 1
    assert result.not_enforced == 1  # the retention SLA


def test_a_running_suite_is_not_a_verdict(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, outcome, "orders")
    _run_with(db_session, orders, "running", {_rule_check(orders, "row-floor").id: "pass"})
    result = svc.compliance(db_session, svc.get_contract(db_session, outcome.contract.id, actor.id))
    orders_suite = next(x for x in result.suites if x.suite_id == orders.id)
    assert orders_suite.results_final is False
    assert {c.state for c in result.clauses if c.suite_id == orders.id} >= {"pass", "not_run"}


def test_errored_disabled_and_edited_clauses_say_so(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, outcome, "orders")
    floor = _rule_check(orders, "row-floor")
    nulls = _rule_check(orders, "amount-nulls")
    nulls.enabled = False
    floor.config = {"min_rows": 500}  # a person's edit
    db_session.commit()
    _run_with(db_session, orders, "succeeded", {floor.id: "error"})
    result = svc.compliance(db_session, svc.get_contract(db_session, outcome.contract.id, actor.id))
    by_rule = {c.clause["rule_id"]: c for c in result.clauses if c.clause["rule_id"]}
    assert by_rule["row-floor"].state == "unverified" and by_rule["row-floor"].drifted is True
    assert by_rule["amount-nulls"].state == "disabled" and by_rule["amount-nulls"].drifted is False


def test_compliance_reads_only_the_suites_the_caller_can_see(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    viewer = _user(db_session)
    db_session.add(
        Share(suite_id=outcome.created_suite_ids[0], user_id=viewer.id, permission="view")
    )
    db_session.commit()
    result = svc.compliance(
        db_session, svc.get_contract(db_session, outcome.contract.id, viewer.id)
    )
    assert {c.suite_id for c in result.clauses} == {outcome.created_suite_ids[0]}
    assert [x.suite_id for x in result.suites] == [outcome.created_suite_ids[0]]


# ───────────────────────────── linking (phase B) ─────────────────────────────


def test_link_suite_sets_both_fields_and_refuses_half_a_link(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    other = Suite(name="mine", connection_id=conn.id, created_by=actor.id)
    db_session.add(other)
    db_session.commit()
    svc.link_suite(
        db_session,
        other,
        contract_id=outcome.contract.id,
        contract_object="orders",
        actor_id=actor.id,
    )
    assert (other.contract_id, other.contract_object) == (outcome.contract.id, "orders")
    with pytest.raises(svc.ContractSuitesInvalidError, match="together"):
        svc.link_suite(
            db_session,
            other,
            contract_id=outcome.contract.id,
            contract_object=None,
            actor_id=actor.id,
        )
    with pytest.raises(svc.ContractSuitesInvalidError, match="no schema object"):
        svc.link_suite(
            db_session,
            other,
            contract_id=outcome.contract.id,
            contract_object="nope",
            actor_id=actor.id,
        )
    svc.link_suite(db_session, other, contract_id=None, contract_object=None, actor_id=actor.id)
    assert (other.contract_id, other.contract_object) == (None, None)


def test_link_suite_refuses_a_contract_the_caller_cannot_see(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    outcome = _import(db_session, owner, conn, _doc())
    stranger = _user(db_session)
    theirs = Suite(name="theirs", connection_id=conn.id, created_by=stranger.id)
    db_session.add(theirs)
    db_session.commit()
    with pytest.raises(svc.ContractNotFoundError):
        svc.link_suite(
            db_session,
            theirs,
            contract_id=outcome.contract.id,
            contract_object="orders",
            actor_id=stranger.id,
        )


# ───────────────────────────── evidence (phase B) ─────────────────────────────


def test_the_evidence_card_names_the_contract_clause(db_session: Any) -> None:
    from backend.app.alerting.render import _contract_clause
    from backend.app.services.incident_evidence import _contract_layer

    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc())
    orders = _suite(db_session, outcome, "orders")
    layer = _contract_layer(db_session, _rule_check(orders, "row-floor"))
    assert layer is not None
    assert (layer["name"], layer["version"]) == ("seller_payments", "1.0.0")
    assert layer["clause"] == {
        "object": "orders",
        "property": None,
        "rule_id": "row-floor",
        "field": "quality",
    }
    assert _contract_clause(layer) == "contract seller_payments 1.0.0 · orders row-floor"

    hand = Check(
        suite_id=orders.id,
        name="mine",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "email"},
    )
    db_session.add(hand)
    db_session.commit()
    assert _contract_layer(db_session, hand) is None
    assert _contract_clause(None) == ""
