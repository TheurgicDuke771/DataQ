"""A check's value set over a column that is sensitive now is withheld on read (#2592)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.mcp import server
from backend.app.services import check_service
from backend.app.services.value_set_guard import ValueSetRedactedError

_MASKED = ["<redacted>", "<redacted>"]


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def owner(db_session: Any) -> Iterator[User]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="admin")
    db_session.add(user)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def suite(db_session: Any, owner: User) -> Suite:
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
    suite = Suite(
        name="orders",
        connection_id=conn.id,
        created_by=owner.id,
        target={"schema": "public", "table": "orders"},
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def _in_set(db: Any, suite: Suite, column: str, values: list[str]) -> Check:
    return check_service.create_check(
        db,
        suite_id=suite.id,
        name=f"{column} in set",
        kind="expectation",
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": column, "value_set": values},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
    )


@pytest.fixture
def classified(db_session: Any, suite: Suite) -> tuple[Check, Check]:
    """A check on `tier`, classified sensitive AFTER the check was written, and one on `status`."""
    tier = _in_set(db_session, suite, "tier", ["gold", "silver"])
    status = _in_set(db_session, suite, "status", ["open", "closed"])
    suite.column_policy = {"pii_columns": ["tier"]}
    db_session.commit()
    return tier, status


def test_the_checks_api_withholds_the_value_set_and_says_why(
    client: TestClient, suite: Suite, classified: tuple[Check, Check]
) -> None:
    tier, status = classified
    listed = {c["id"]: c for c in client.get(f"/api/v1/suites/{suite.id}/checks").json()}
    one = client.get(f"/api/v1/suites/{suite.id}/checks/{tier.id}").json()

    for read in (listed[str(tier.id)], one):
        assert read["config"] == {"column": "tier", "value_set": _MASKED}
        assert (read["redacted_columns"], read["redaction_reasons"]) == (
            ["tier"],
            {"tier": "policy"},
        )
    assert listed[str(status.id)]["config"]["value_set"] == ["open", "closed"]
    assert listed[str(status.id)]["redacted_columns"] == []


def test_saving_the_masked_value_set_back_is_refused_and_changes_nothing(
    client: TestClient, db_session: Any, suite: Suite, classified: tuple[Check, Check]
) -> None:
    tier, _ = classified
    masked = client.get(f"/api/v1/suites/{suite.id}/checks/{tier.id}").json()["config"]

    refused = client.patch(f"/api/v1/suites/{suite.id}/checks/{tier.id}", json={"config": masked})

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "value_set_redacted"
    db_session.expire_all()
    assert db_session.get(Check, tier.id).config["value_set"] == ["gold", "silver"]


def test_an_edit_without_config_keeps_the_real_values_and_a_full_list_replaces_them(
    client: TestClient, db_session: Any, suite: Suite, classified: tuple[Check, Check]
) -> None:
    tier, _ = classified
    path = f"/api/v1/suites/{suite.id}/checks/{tier.id}"

    renamed = client.patch(path, json={"name": "tier is known"})
    assert renamed.status_code == 200
    db_session.expire_all()
    assert db_session.get(Check, tier.id).config["value_set"] == ["gold", "silver"]

    replaced = {"column": "tier", "value_set": ["gold", "silver", "bronze"]}
    saved = client.patch(path, json={"config": replaced})
    assert saved.status_code == 200
    db_session.expire_all()
    assert db_session.get(Check, tier.id).config["value_set"] == ["gold", "silver", "bronze"]


def test_creating_a_check_with_the_marker_is_refused(db_session: Any, suite: Suite) -> None:
    with pytest.raises(ValueSetRedactedError) as excinfo:
        _in_set(db_session, suite, "tier", _MASKED)
    assert excinfo.value.code == "value_set_redacted"


def test_the_version_history_withholds_it_too(
    client: TestClient, suite: Suite, classified: tuple[Check, Check]
) -> None:
    tier, _ = classified
    (version,) = client.get(f"/api/v1/suites/{suite.id}/checks/{tier.id}/versions").json()

    assert version["config"]["value_set"] == _MASKED
    assert version["redacted_columns"] == ["tier"]


def test_an_export_withholds_it_and_its_import_is_refused(
    client: TestClient, suite: Suite, classified: tuple[Check, Check]
) -> None:
    document = client.get(f"/api/v1/suites/{suite.id}/export").json()
    by_name = {c["name"]: c for c in document["checks"]}

    assert by_name["tier in set"]["config"]["value_set"] == _MASKED
    assert by_name["status in set"]["config"]["value_set"] == ["open", "closed"]

    refused = client.post(
        "/api/v1/suites/import",
        json={"document": document, "connection_id": str(suite.connection_id)},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "value_set_redacted"


def test_the_mcp_check_tools_withhold_it(
    db_session: Any,
    monkeypatch: Any,
    owner: User,
    suite: Suite,
    classified: tuple[Check, Check],
) -> None:
    tier, _ = classified
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: owner)

    listed = {c["id"]: c for c in server.list_checks(str(suite.id))["checks"]}
    one = server.get_check(str(suite.id), str(tier.id))
    (version,) = server.list_check_versions(str(suite.id), str(tier.id))["versions"]
    exported = {c["name"]: c for c in server.export_suite(str(suite.id))["checks"]}

    for config in (listed[str(tier.id)]["config"], one["config"], version["config"]):
        assert config["value_set"] == _MASKED
    assert exported["tier in set"]["config"]["value_set"] == _MASKED
    assert (one["redacted_columns"], one["redaction_reasons"]) == (["tier"], {"tier": "policy"})


def _check(
    db: Any, suite: Suite, expectation_type: str, config: dict[str, Any], **kw: Any
) -> Check:
    return check_service.create_check(
        db,
        suite_id=suite.id,
        name=f"{expectation_type} {uuid.uuid4().hex[:6]}",
        kind=kw.get("kind", "expectation"),
        expectation_type=expectation_type,
        config=config,
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
    )


#: (expectation type, kind, config, the keys that must read withheld) — #2682.
_WITHHELD = [
    ("expect_column_values_to_match_regex", "expectation", {"regex": "^GOLD-[0-9]+$"}, ["regex"]),
    (
        "expect_column_values_to_not_match_regex_list",
        "expectation",
        {"regex_list": ["^A$", "^B$"]},
        ["regex_list"],
    ),
    (
        "expect_column_values_to_be_between",
        "expectation",
        {"min_value": 50000, "max_value": 250000},
        ["min_value", "max_value"],
    ),
    ("monitor:aggregate", "aggregate", {"aggregate": "max", "max_value": 250000}, ["max_value"]),
]
#: Bounds that are counts or lengths, not values of the column: shown.
_SHOWN = [
    ("expect_column_value_lengths_to_be_between", "expectation", {"min_value": 2, "max_value": 9}),
    ("monitor:aggregate", "aggregate", {"aggregate": "mean", "max_value": 100}),
]


@pytest.mark.parametrize(("etype", "kind", "config", "keys"), _WITHHELD)
def test_patterns_and_value_bounds_of_a_sensitive_column_are_withheld_on_read(
    client: TestClient,
    db_session: Any,
    suite: Suite,
    etype: str,
    kind: str,
    config: dict[str, Any],
    keys: list[str],
) -> None:
    check = _check(db_session, suite, etype, {"column": "tier", **config}, kind=kind)
    suite.column_policy = {"pii_columns": ["tier"]}
    db_session.commit()
    path = f"/api/v1/suites/{suite.id}/checks/{check.id}"

    read = client.get(path).json()
    for key in keys:
        assert "<redacted>" in str(read["config"][key])
        assert str(config[key]) not in str(read["config"][key])
    assert read["redacted_columns"] == ["tier"]
    (version,) = client.get(f"{path}/versions").json()
    assert all("<redacted>" in str(version["config"][key]) for key in keys)
    exported = client.get(f"/api/v1/suites/{suite.id}/export").json()
    assert not [c for c in exported["checks"] if str(config[keys[0]]) in str(c["config"])]

    # Saving the withheld read back is refused; the stored values stay.
    refused = client.patch(path, json={"config": read["config"]})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "value_set_redacted"
    db_session.expire_all()
    assert db_session.get(Check, check.id).config[keys[0]] == config[keys[0]]


@pytest.mark.parametrize(("etype", "kind", "config"), _SHOWN)
def test_bounds_that_are_counts_or_lengths_are_shown(
    client: TestClient,
    db_session: Any,
    suite: Suite,
    etype: str,
    kind: str,
    config: dict[str, Any],
) -> None:
    check = _check(db_session, suite, etype, {"column": "tier", **config}, kind=kind)
    suite.column_policy = {"pii_columns": ["tier"]}
    db_session.commit()

    read = client.get(f"/api/v1/suites/{suite.id}/checks/{check.id}").json()
    assert {k: read["config"][k] for k in config} == config
    assert read["redacted_columns"] == []


def test_a_value_set_that_is_not_a_list_is_withheld_too(
    client: TestClient, db_session: Any, suite: Suite, monkeypatch: Any
) -> None:
    """A producer or an old document can hold a bare value where a list belongs."""
    check = _in_set(db_session, suite, "tier", ["gold"])
    db_session.get(Check, check.id).config = {"column": "tier", "value_set": "gold"}
    suite.column_policy = {"pii_columns": ["tier"]}
    db_session.commit()

    read = client.get(f"/api/v1/suites/{suite.id}/checks/{check.id}").json()
    assert read["config"]["value_set"] == "<redacted>"
