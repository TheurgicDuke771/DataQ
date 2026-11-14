"""A contract's value lists over a column that is sensitive now: withheld on export, refused on
import (#2615)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from backend.app.contracts import odcs, redaction
from backend.app.contracts import translate as tr
from backend.app.contracts.export import export_contract
from backend.app.db.models import Check, Share
from backend.app.services import contract_service as svc
from backend.app.services.value_set_guard import ValueSetRedactedError
from backend.tests.services.test_contract_service import (
    _connection,
    _doc,
    _import,
    _suite,
    _user,
)

_M = "<redacted>"
#: Literals that appear nowhere else in the document, so a leak is findable in its JSON.
_SECRETS = (
    "tier-gold",
    "tier-silver",
    "tier-lead",
    "ex-platinum",
    "n/a-tier",
    "gx-opal",
)


def _tier_property() -> dict[str, Any]:
    """`tier` lists its values in every place an ODCS document can."""
    return {
        "name": "tier",
        "logicalType": "string",
        "enum": [{"value": "tier-gold", "description": "top"}, {"value": "tier-silver"}],
        "examples": ["ex-platinum"],
        "quality": [
            {
                "id": "tier-valid",
                "metric": "invalidValues",
                "arguments": {"validValues": ["tier-gold", "tier-silver", "tier-lead"]},
                "mustBe": 0,
            },
            {
                "id": "tier-missing",
                "metric": "missingValues",
                "arguments": {"missingValues": ["n/a-tier"]},
                "mustBe": 0,
            },
            {
                "id": "tier-gx",
                "type": "custom",
                "engine": "greatExpectations",
                # A string implementation, as a producer's YAML often carries it.
                "implementation": json.dumps(
                    {
                        "expectation_type": "expect_column_values_to_not_be_in_set",
                        "kwargs": {"value_set": ["gx-opal"]},
                    }
                ),
            },
        ],
    }


def _doc_with_tier(version: str = "1.0.0") -> dict[str, Any]:
    doc = _doc(version)
    doc["schema"][0]["properties"].append(_tier_property())
    return doc


def _classify(db: Any, suite: Any, column: str) -> None:
    policy = dict(suite.column_policy or {})
    policy["pii_columns"] = [*policy.get("pii_columns", []), column]
    suite.column_policy = policy
    db.commit()


def _export(db: Any, outcome: svc.ImportOutcome, user: Any) -> dict[str, Any]:
    visible = svc.get_contract(db, outcome.contract.id, user.id)
    return export_contract(db, visible.contract, visible.suites)


def _tier(exported: dict[str, Any]) -> dict[str, Any]:
    tier: dict[str, Any] = next(
        p for p in exported["schema"][0]["properties"] if p["name"] == "tier"
    )
    return tier


def _custom(node: dict[str, Any], name: str) -> Any:
    return next(
        (c["value"] for c in node.get("customProperties", []) if c["property"] == f"dataq.{name}"),
        None,
    )


def test_values_are_exported_while_the_column_is_not_sensitive(db_session: Any) -> None:
    actor = _user(db_session)
    outcome = _import(db_session, actor, _connection(db_session, actor), _doc_with_tier())
    exported = _export(db_session, outcome, actor)
    text = json.dumps(exported)
    assert all(secret in text for secret in _SECRETS) and _M not in text
    assert _custom(exported, "redactedColumns") is None
    assert _custom(_tier(exported), "valuesWithheld") is None
    # The string implementation is left as the producer wrote it.
    assert isinstance(_tier(exported)["quality"][2]["implementation"], str)


def test_every_value_list_is_withheld_once_the_column_is_classified(db_session: Any) -> None:
    actor = _user(db_session)
    outcome = _import(db_session, actor, _connection(db_session, actor), _doc_with_tier())
    _classify(db_session, _suite(db_session, outcome, "orders"), "tier")

    exported = _export(db_session, outcome, actor)
    odcs.validate_document(exported)  # still a valid ODCS document
    text = json.dumps(exported)
    assert not [secret for secret in _SECRETS if secret in text]

    tier = _tier(exported)
    assert tier["enum"] == [{"value": _M}]  # `enum` entries must be unique: one, counted below
    assert tier["examples"] == [_M]
    valid, missing, gx = tier["quality"]
    assert valid["arguments"]["validValues"] == [_M, _M, _M]
    assert missing["arguments"]["missingValues"] == [_M]
    assert gx["implementation"]["kwargs"]["value_set"] == [_M]

    # The document says what was withheld, where, and why — never "there was no list".
    assert _custom(tier, "valuesWithheld") == {
        "column": "tier",
        "reason": "policy",
        "fields": {"enum": 2, "examples": 1},
    }
    assert _custom(valid, "valuesWithheld") == {
        "column": "tier",
        "reason": "policy",
        "fields": {"arguments.validValues": 3},
    }
    assert _custom(gx, "valuesWithheld")["fields"] == {"implementation.kwargs.value_set": 1}
    assert _custom(exported, "redactedColumns") == [
        {"object": "orders", "column": "tier", "reason": "policy"}
    ]
    assert redaction.redacted_columns(exported) == {"orders.tier": "policy"}
    # The checks still hold, and the stored document still is, the real values.
    stored = json.dumps(outcome.contract.spec)
    assert all(secret in stored for secret in _SECRETS) and _M not in stored
    # The rules are still matched to their checks (masking runs after the lookup).
    assert _custom(valid, "checkId") is not None


def test_a_reader_who_cannot_see_the_suite_gets_the_values_withheld_too(db_session: Any) -> None:
    owner = _user(db_session)
    outcome = _import(db_session, owner, _connection(db_session, owner), _doc_with_tier())
    _classify(db_session, _suite(db_session, outcome, "orders"), "tier")
    viewer = _user(db_session)
    db_session.add(
        Share(
            suite_id=_suite(db_session, outcome, "refunds").id,
            user_id=viewer.id,
            permission="view",
        )
    )
    db_session.commit()

    exported = _export(db_session, outcome, viewer)  # sees `refunds` only
    assert not [secret for secret in _SECRETS if secret in json.dumps(exported)]
    assert _tier(exported)["enum"] == [{"value": _M}]


def test_an_object_no_suite_backs_is_held_to_the_documents_own_classification(
    db_session: Any,
) -> None:
    actor = _user(db_session)
    doc = _doc_with_tier()
    doc["schema"][0]["properties"][-1]["classification"] = "restricted"
    outcome = _import(db_session, actor, _connection(db_session, actor), doc)
    orders = _suite(db_session, outcome, "orders")
    orders.contract_id = orders.contract_object = None  # unlinked by hand
    db_session.commit()
    db_session.refresh(outcome.contract)

    exported = _export(db_session, outcome, actor)
    assert not [secret for secret in _SECRETS if secret in json.dumps(exported)]


def test_a_hand_added_check_is_noted_with_the_reason_the_suite_export_withheld_it_for(
    db_session: Any,
) -> None:
    actor = _user(db_session)
    outcome = _import(db_session, actor, _connection(db_session, actor), _doc())
    orders = _suite(db_session, outcome, "orders")
    db_session.add(
        Check(
            suite_id=orders.id,
            name="channel allowed",
            kind="expectation",
            expectation_type="expect_column_values_to_be_in_set",
            config={"column": "channel", "value_set": ["chan-web", "chan-app"]},
            ordinal=50,
        )
    )
    _classify(db_session, orders, "channel")

    exported = _export(db_session, outcome, actor)
    rule = next(r for r in exported["schema"][0]["quality"] if r.get("engine") == "dataq")
    assert rule["implementation"]["config"]["value_set"] == [_M, _M]
    assert _custom(rule, "valuesWithheld") == {
        "column": "channel",
        "reason": "policy",
        "fields": {"implementation.config.value_set": 2},
    }
    assert redaction.redacted_columns(exported) == {"orders.channel": "policy"}


def test_an_export_with_withheld_values_cannot_be_imported(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _connection(db_session, actor)
    outcome = _import(db_session, actor, conn, _doc_with_tier())
    orders = _suite(db_session, outcome, "orders")
    _classify(db_session, orders, "tier")
    exported = _export(db_session, outcome, actor)
    before = copy.deepcopy(outcome.contract.spec)
    enabled = {c.id: c.enabled for c in orders.checks}

    # Replacing the stored version would overwrite the real values and switch the checks off.
    with pytest.raises(ValueSetRedactedError) as excinfo:
        _import(db_session, actor, conn, exported, replace=True)
    assert excinfo.value.code == "value_set_redacted"
    assert (excinfo.value.detail or {})["column"] == "tier"
    # …and so would a new version reconciling the same suites.
    with pytest.raises(ValueSetRedactedError):
        _import(db_session, actor, conn, {**exported, "version": "2.0.0"}, suite_ids=[orders.id])

    db_session.refresh(outcome.contract)
    db_session.refresh(orders)
    assert outcome.contract.spec == before
    assert {c.id: c.enabled for c in orders.checks} == enabled


@pytest.mark.parametrize(
    "place",
    [
        {"enum": [{"value": _M}]},
        {"examples": [_M]},
        {"quality": [{"metric": "invalidValues", "arguments": {"validValues": [_M]}}]},
        {"quality": [{"metric": "missingValues", "arguments": {"missingValues": ["x", _M]}}]},
        {"quality": [{"rule": "validValues", "validValues": [_M]}]},
        {
            "quality": [
                {
                    "type": "custom",
                    "engine": "dataq",
                    "implementation": {
                        "expectation_type": "expect_column_values_to_be_in_set",
                        "config": {"column": "tier", "value_set": [_M]},
                    },
                }
            ]
        },
        {"properties": [{"name": "inner", "enum": [{"value": _M}]}]},
    ],
)
def test_the_marker_is_refused_in_every_place_a_value_list_lives(place: dict[str, Any]) -> None:
    doc = _doc()
    doc["schema"][0]["properties"].append({"name": "tier", "logicalType": "string", **place})
    with pytest.raises(ValueSetRedactedError):
        redaction.reject_masked_document(doc)


def test_a_document_without_the_marker_is_not_refused() -> None:
    redaction.reject_masked_document(_doc_with_tier())


def test_the_v30_spelling_is_withheld_in_both_places(db_session: Any) -> None:
    """A v3.0 `rule: validValues` keeps its list beside the `arguments` copy normalisation adds."""
    rule = {"rule": "validValues", "validValues": ["v30-jade"], "mustBe": 0}
    prop = {"name": "tier", "classification": "restricted", "quality": [rule]}
    doc = tr.normalize_document({"schema": [{"name": "orders", "properties": [prop]}]})

    assert redaction.mask_document(db_session, doc, []) == {"orders.tier": "policy"}
    masked = doc["schema"][0]["properties"][0]["quality"][0]
    assert masked["validValues"] == [_M] and masked["arguments"]["validValues"] == [_M]
    assert "v30-jade" not in json.dumps(doc)
