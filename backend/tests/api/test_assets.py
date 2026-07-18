"""Asset view API tests against a real Postgres (db_session) via TestClient.

The **authz matrix is the point** (ADR 0034 decision 5 / ADR 0027): asset
visibility is derived from suite grants, never granted directly. This exercises
owner / edit-share / view-share / no-share / workspace-admin — plus partial-grant
aggregation filtering and 404-no-leak — end to end through the HTTP surface.

The ADR-0033 **Viewer** role is N/A here: it is not built yet (#740-#743 open), so
the ladder under test is owner / edit / view / no-share / workspace-admin. When
Viewer lands (#741) it caps at `view`, so it will fold into the view-share rows.

Skips without TEST_DATABASE_URL (JSONB/UUID need real Postgres).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Asset, Check, LineageEdge, Result, Run, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import suite_service

# A fully-resolvable snowflake config (account+database+schema) so a suite target
# resolves to a first-class asset (ADR 0034). Two suites on this connection with
# the same target resolve to the SAME asset.
_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}
_ADMIN_EMAIL = "admin@example.com"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db_session: Any, email: str) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db_session.add(u)
    db_session.flush()
    return u


def _connection(db_session: Any, owner: User) -> Any:
    from backend.app.db.models import Connection

    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _suite(db_session: Any, owner: User, conn: Any, *, name: str, table: str) -> Suite:
    """Create a suite via the service so its target resolves to an asset."""
    return suite_service.create_suite(
        db_session,
        name=name,
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )


def _seed_run(db_session: Any, suite: Suite, *, status: str = "fail") -> Run:
    """One succeeded run on `suite` carrying a single check result of `status`."""
    check = Check(
        suite_id=suite.id,
        name=f"c-{uuid.uuid4().hex[:6]}",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "x"},
    )
    db_session.add(check)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="t", asset_id=suite.asset_id)
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status=status))
    db_session.commit()
    return run


def _share(db_session: Any, suite: Suite, user: User, permission: str) -> None:
    db_session.add(Share(suite_id=suite.id, user_id=user.id, permission=permission))
    db_session.commit()


# ── the shared world: asset X (2 suites), asset Y (1 suite) ──────────────────


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = _user(db_session, "owner@example.com")
    conn = _connection(db_session, owner)
    s1 = _suite(db_session, owner, conn, name="Orders quality", table="ORDERS")
    s2 = _suite(db_session, owner, conn, name="Orders volume", table="ORDERS")
    s3 = _suite(db_session, owner, conn, name="Customers", table="CUSTOMERS")
    # s1 + s2 share the ORDERS asset; s3 is a distinct asset.
    assert s1.asset_id is not None and s1.asset_id == s2.asset_id
    assert s3.asset_id is not None and s3.asset_id != s1.asset_id
    _seed_run(db_session, s1, status="fail")
    return {
        "owner": owner,
        "conn": conn,
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "asset_x": s1.asset_id,
        "asset_y": s3.asset_id,
    }


# ── list authz ───────────────────────────────────────────────────────────────


def test_owner_sees_both_assets_with_full_suite_counts(
    client: TestClient, world: dict[str, Any]
) -> None:
    _as(world["owner"])
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    by_id = {a["id"]: a for a in resp.json()}
    assert str(world["asset_x"]) in by_id and str(world["asset_y"]) in by_id
    # Asset X composes BOTH orders suites for the owner (full grants).
    assert by_id[str(world["asset_x"])]["suite_count"] == 2
    assert by_id[str(world["asset_y"])]["suite_count"] == 1
    # The failing run rolls up into the asset health.
    assert by_id[str(world["asset_x"])]["worst_severity"] == "fail"


def test_view_share_partial_grant_filters_aggregation(
    client: TestClient, world: dict[str, Any]
) -> None:
    """A view-share on ONLY s1 sees asset X but with suite_count=1 (s2 filtered
    out), and never sees asset Y — the partial-grant aggregation-filtering rule."""
    viewer = _user(client_db(client), "viewer@example.com")
    _share(client_db(client), world["s1"], viewer, "view")
    _as(viewer)
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    by_id = {a["id"]: a for a in resp.json()}
    assert str(world["asset_x"]) in by_id
    # #920: asset Y (no grant) is present but REDACTED — never omitted, never named.
    y = by_id[str(world["asset_y"])]
    assert y["is_accessible"] is False and y["name"] is None and y["suite_count"] == 0
    assert by_id[str(world["asset_x"])]["suite_count"] == 1  # s2 filtered out


def test_edit_share_sees_asset(client: TestClient, world: dict[str, Any]) -> None:
    editor = _user(client_db(client), "editor@example.com")
    _share(client_db(client), world["s1"], editor, "edit")
    _as(editor)
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    assert str(world["asset_x"]) in {a["id"] for a in resp.json()}


def test_no_share_sees_only_redacted_rows(client: TestClient, world: dict[str, Any]) -> None:
    # #920 superseded the old empty-browse rule: a no-grant caller sees that assets
    # EXIST (redacted rows — omission would assert an empty workspace) but learns no
    # identity or health about any of them.
    outsider = _user(client_db(client), "outsider@example.com")
    _as(outsider)
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows  # the monitored assets exist — as anonymous entries
    assert all(r["is_accessible"] is False and r["name"] is None for r in rows)
    for leaf in ("ORDERS", "CUSTOMERS"):
        assert f'"{leaf}"' not in resp.text  # no identity leaks, only prefixes


def test_workspace_admin_sees_all(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    by_id = {a["id"]: a for a in resp.json()}
    assert str(world["asset_x"]) in by_id and str(world["asset_y"]) in by_id
    assert by_id[str(world["asset_x"])]["suite_count"] == 2  # admin sees every suite


# ── detail authz + no-leak ───────────────────────────────────────────────────


def test_owner_detail_has_both_suites(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["suites"]) == 2  # health across ≥2 suites (the #760 criterion)
    assert {s["my_permission"] for s in body["suites"]} == {"owner"}


def test_view_share_detail_filtered_to_one_suite(client: TestClient, world: dict[str, Any]) -> None:
    viewer = _user(client_db(client), "viewer2@example.com")
    _share(client_db(client), world["s1"], viewer, "view")
    _as(viewer)
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["suites"]) == 1
    assert body["suites"][0]["suite_id"] == str(world["s1"].id)
    assert body["suites"][0]["my_permission"] == "view"


def test_no_share_detail_404_no_leak(client: TestClient, world: dict[str, Any]) -> None:
    outsider = _user(client_db(client), "outsider2@example.com")
    _as(outsider)
    # The asset EXISTS but is wholly outside the caller's grants → 404 (not 403).
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 404


def test_unknown_asset_404(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_404_no_leak_bodies_identical(client: TestClient, world: dict[str, Any]) -> None:
    """No-leak means indistinguishable: an existing-but-ungranted asset and a
    truly unknown id must return the same status AND the same body shape — the
    only permitted variation is the probed id echoed back in the detail."""
    outsider = _user(client_db(client), "outsider3@example.com")
    _as(outsider)
    unknown_id = uuid.uuid4()
    existing = client.get(f"/api/v1/assets/{world['asset_x']}")
    unknown = client.get(f"/api/v1/assets/{unknown_id}")
    assert existing.status_code == unknown.status_code == 404

    def normalized(resp: Any) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = resp.json()
        echoed: str = body["error"]["detail"].pop("asset_id")
        return echoed, body

    existing_echo, existing_body = normalized(existing)
    unknown_echo, unknown_body = normalized(unknown)
    # The echoed id must be exactly the probed one (no other id leaks) …
    assert existing_echo == str(world["asset_x"])
    assert unknown_echo == str(unknown_id)
    # … and everything else must be byte-identical between the two cases.
    assert existing_body == unknown_body


def test_garbage_uuid_is_422_not_500(client: TestClient, world: dict[str, Any]) -> None:
    """Malformed path/query input is a validation error, never a 500 (#570 class)."""
    _as(world["owner"])
    assert client.get("/api/v1/assets/not-a-uuid").status_code == 422
    assert client.get("/api/v1/assets/%00").status_code == 422
    assert client.patch("/api/v1/assets/definitely-garbage", json={}).status_code in (403, 422)
    # List query params validate too: limit/offset outside their bounds.
    assert client.get("/api/v1/assets", params={"limit": "abc"}).status_code == 422
    assert client.get("/api/v1/assets", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/assets", params={"limit": 201}).status_code == 422
    assert client.get("/api/v1/assets", params={"offset": -1}).status_code == 422


def test_list_pagination_stable_slices(client: TestClient, world: dict[str, Any]) -> None:
    """limit/offset slice the stable (namespace, name) ordering deterministically."""
    _as(world["owner"])
    full = client.get("/api/v1/assets").json()
    assert len(full) == 2
    page1 = client.get("/api/v1/assets", params={"limit": 1, "offset": 0}).json()
    page2 = client.get("/api/v1/assets", params={"limit": 1, "offset": 1}).json()
    assert len(page1) == 1 and len(page2) == 1
    assert [a["id"] for a in full] == [page1[0]["id"], page2[0]["id"]]
    # Past the end → empty page, not an error.
    assert client.get("/api/v1/assets", params={"offset": 5}).json() == []


def test_summary_flags_failed_and_active_runs(client: TestClient, world: dict[str, Any]) -> None:
    """An operationally-failed latest run (no results → no severity) and an
    in-flight run surface as summary flags so the UI never rolls them up green."""
    db = client_db(client)
    db.add(
        Run(
            suite_id=world["s2"].id,
            status="failed",
            triggered_by="t-failed",
            asset_id=world["s2"].asset_id,
        )
    )
    db.add(
        Run(
            suite_id=world["s3"].id,
            status="queued",
            triggered_by="t-queued",
            asset_id=world["s3"].asset_id,
        )
    )
    db.commit()
    _as(world["owner"])
    by_id = {a["id"]: a for a in client.get("/api/v1/assets").json()}
    asset_x = by_id[str(world["asset_x"])]
    assert asset_x["has_failed_run"] is True  # s2's latest run failed
    asset_y = by_id[str(world["asset_y"])]
    assert asset_y["has_active_run"] is True  # s3's latest run is queued
    assert asset_y["has_failed_run"] is False


def test_redacted_neighbour_identity_never_reaches_the_response_body(
    client: TestClient, world: dict[str, Any]
) -> None:
    """The claim #845 makes is that a restricted neighbour's identity **never crosses the
    wire** — so assert it at the wire, not at the DTO one layer above it.

    `LineageNodeRead` is a separate Pydantic model from the service dataclass. If someone
    later re-tightens `name: str` with an empty-string fallback, or a serializer starts
    deriving a display name, the leak would ship with a fully green suite unless something
    inspects the raw body. This does."""
    db = client_db(client)
    stranger = _user(db, "stranger2@example.com")
    conn = _connection(db, stranger)
    secret = _suite(db, stranger, conn, name="Secret mart", table="MART_SECRET_REVENUE")
    assert secret.asset_id is not None
    secret_asset = db.get(Asset, secret.asset_id)
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_x"],
            downstream_asset_id=secret.asset_id,
            source="dbt",
        )
    )
    db.commit()

    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200

    node = next(n for n in resp.json()["downstream"] if n["id"] == str(secret.asset_id))
    assert node["is_accessible"] is False
    assert node["name"] is None and node["namespace"] is None and node["env"] is None
    assert node["is_monitored"] is False
    # The raw body — the actual wire. The name must survive nowhere in it.
    assert "MART_SECRET_REVENUE" not in resp.text
    assert secret_asset is not None and secret_asset.name not in resp.text


def test_asset_with_only_unshared_suites_is_404_and_unlisted(
    client: TestClient, world: dict[str, Any]
) -> None:
    """The grant boundary that DOES stay closed (#845/#846, amended by #920).

    An asset that someone *else* monitors, whose suites the caller cannot view, now
    APPEARS in browse — but only as a redacted row (#920: omission asserted "nothing
    else exists here"); its identity never crosses, and the detail endpoint is still
    404-no-leak. The browse redaction, the graph redaction, and the 404 are one rule —
    if they ever disagree the graph offers a dead link again."""
    db = client_db(client)
    stranger = _user(db, "stranger@example.com")
    conn = _connection(db, stranger)
    secret_suite = _suite(db, stranger, conn, name="Secret revenue", table="MART_REVENUE")
    assert secret_suite.asset_id is not None

    _as(world["owner"])
    resp = client.get("/api/v1/assets")
    by_id = {a["id"]: a for a in resp.json()}
    row = by_id[str(secret_suite.asset_id)]
    assert row["is_accessible"] is False and row["name"] is None
    assert "MART_REVENUE" not in resp.text
    assert client.get(f"/api/v1/assets/{secret_suite.asset_id}").status_code == 404


def test_orphan_asset_after_composing_suite_deleted(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """Deleting an asset's only composing suite (after it ran) orphans the asset. It stays
    visible — to **everyone**, not just admins (ADR 0034 amendment, #845/#846) — with an
    empty suites list and no health.

    This reverses the earlier rule (orphans hidden from non-admins), deliberately. A
    suite-less asset has no suites, runs, results or samples behind it — the delete
    cascaded all of it (#540) — so there is no grant to protect and nothing to leak but
    the *name*, which the lineage graph reveals the existence of regardless. Hiding it
    bought no security and cost real correctness: browse and the detail endpoint
    disagreed about what existed, and every unmonitored upstream in a lineage graph would
    have rendered "restricted" to a non-admin when it is nothing of the sort.

    The grant boundary that *does* stay closed is an asset with suites the caller cannot
    view — see `test_asset_with_only_unshared_suites_is_404_and_unlisted`."""
    db = client_db(client)
    _seed_run(db, world["s3"], status="pass")  # the suite has run history
    suite_service.delete_suite(db, world["s3"].id)  # cascades runs/results (#540)

    _as(world["owner"])
    listed = {a["id"] for a in client.get("/api/v1/assets").json()}
    assert str(world["asset_y"]) in listed  # browse shows what exists
    resp = client.get(f"/api/v1/assets/{world['asset_y']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["suites"] == []
    assert body["summary"]["suite_count"] == 0
    assert body["summary"]["last_run_at"] is None  # run history died with the suite

    admin = _user(db, _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    admin_body = client.get(f"/api/v1/assets/{world['asset_y']}").json()
    assert admin_body["suites"] == []


# ── lineage in detail ────────────────────────────────────────────────────────


def test_detail_includes_lineage_nodes(client: TestClient, world: dict[str, Any]) -> None:
    db = client_db(client)
    # An upstream asset (no suite → not monitored) and a downstream one (monitored
    # = asset Y, which has s3).
    upstream = Asset(namespace="snowflake://ab12345.eu-west-1", name="RAW.ORDERS")
    db.add(upstream)
    db.flush()
    conn_id = world["conn"].id
    db.add(
        LineageEdge(
            upstream_asset_id=upstream.id,
            downstream_asset_id=world["asset_x"],
            source="dbt",
            connection_id=conn_id,
        )
    )
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_x"],
            downstream_asset_id=world["asset_y"],
            source="dbt",
            connection_id=conn_id,
        )
    )
    db.commit()
    _as(world["owner"])
    body = client.get(f"/api/v1/assets/{world['asset_x']}").json()
    up = {n["name"]: n for n in body["upstream"]}
    down = {n["name"]: n for n in body["downstream"]}
    assert "RAW.ORDERS" in up and up["RAW.ORDERS"]["is_monitored"] is False
    assert (
        "ANALYTICS.PUBLIC.CUSTOMERS" in down
        and down["ANALYTICS.PUBLIC.CUSTOMERS"]["is_monitored"] is True
    )


def _edge(db: Any, up: uuid.UUID, down: uuid.UUID, conn_id: uuid.UUID) -> LineageEdge:
    return LineageEdge(
        upstream_asset_id=up, downstream_asset_id=down, source="dbt", connection_id=conn_id
    )


def test_lineage_cycle_terminates_and_dedupes(client: TestClient, world: dict[str, Any]) -> None:
    """A cycle in `lineage_edges` (X → Y → X) must not hang the BFS; the peer
    node appears exactly once per direction (reachable BOTH upstream and
    downstream), and the start asset never lists itself."""
    db = client_db(client)
    conn_id = world["conn"].id
    db.add(_edge(db, world["asset_x"], world["asset_y"], conn_id))
    db.add(_edge(db, world["asset_y"], world["asset_x"], conn_id))
    db.commit()
    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200
    body = resp.json()
    assert [n["id"] for n in body["upstream"]] == [str(world["asset_y"])]
    assert [n["id"] for n in body["downstream"]] == [str(world["asset_y"])]


def test_lineage_depth_cap_respected(client: TestClient, world: dict[str, Any]) -> None:
    """A 12-hop downstream chain is cut at the BFS depth cap (10 hops)."""
    db = client_db(client)
    conn_id = world["conn"].id
    chain: list[uuid.UUID] = [world["asset_x"]]
    for i in range(12):
        node = Asset(namespace="snowflake://ab12345.eu-west-1.aws", name=f"CHAIN.N{i:02d}")
        db.add(node)
        db.flush()
        db.add(_edge(db, chain[-1], node.id, conn_id))
        chain.append(node.id)
    db.commit()
    _as(world["owner"])
    body = client.get(f"/api/v1/assets/{world['asset_x']}").json()
    down_ids = [n["id"] for n in body["downstream"]]
    # 10 hops reachable; hops 11 and 12 are beyond the cap.
    assert down_ids == [str(a) for a in chain[1:11]]
    assert str(chain[11]) not in down_ids and str(chain[12]) not in down_ids


# ── PATCH metadata: workspace-admin-only ─────────────────────────────────────


def test_patch_metadata_forbidden_for_owner_non_admin(
    client: TestClient, world: dict[str, Any]
) -> None:
    # The SUITE owner is still NOT a workspace-admin → metadata mutation is 403.
    _as(world["owner"])
    resp = client.patch(f"/api/v1/assets/{world['asset_x']}", json={"description": "hi"})
    assert resp.status_code == 403


def test_patch_metadata_admin_sets_owner_and_description(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.patch(
        f"/api/v1/assets/{world['asset_x']}",
        json={"description": "The canonical orders table", "owner_user_id": str(admin.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "The canonical orders table"
    assert body["owner_user_id"] == str(admin.id)


def test_patch_metadata_unknown_asset_404(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.patch(f"/api/v1/assets/{uuid.uuid4()}", json={"description": "x"})
    assert resp.status_code == 404


def test_patch_null_vs_omitted_field_semantics(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """Through the real route: an OMITTED field is left untouched, an explicit
    `null` clears it (`model_fields_set` discrimination)."""
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    url = f"/api/v1/assets/{world['asset_x']}"
    seeded = client.patch(url, json={"description": "d1", "owner_user_id": str(admin.id)})
    assert seeded.status_code == 200

    # Omit description, null the owner: description survives, owner clears.
    resp = client.patch(url, json={"owner_user_id": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "d1"
    assert body["owner_user_id"] is None

    # Explicit null description clears it; omitted owner stays cleared.
    resp = client.patch(url, json={"description": None})
    assert resp.status_code == 200
    assert resp.json()["description"] is None

    # Empty body = touch nothing.
    resp = client.patch(url, json={})
    assert resp.status_code == 200
    assert resp.json()["description"] is None


def test_patch_unknown_field_rejected(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """`extra="forbid"`: a typo'd field must 422, not silently no-op — with
    omitted-vs-null semantics a swallowed typo would read as 'leave unchanged'."""
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.patch(f"/api/v1/assets/{world['asset_x']}", json={"descripton": "typo"})
    assert resp.status_code == 422


def test_patch_owner_must_exist(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """A non-existent owner_user_id is a clean 422 (FK pre-check), never a 500."""
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.patch(
        f"/api/v1/assets/{world['asset_x']}", json={"owner_user_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "asset_owner_invalid"


def test_patch_nul_byte_and_oversized_description_422(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """The #570 guard class: NUL bytes and over-cap strings are 422s, never 500s."""
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    url = f"/api/v1/assets/{world['asset_x']}"
    assert client.patch(url, json={"description": "bad\x00value"}).status_code == 422
    assert client.patch(url, json={"description": "x" * 1025}).status_code == 422
    # The cap boundary itself is accepted.
    assert client.patch(url, json={"description": "x" * 1024}).status_code == 200


# ── asset_id exposed on SuiteRead / RunRead (deferred to #760 by #764) ───────


def test_suite_read_exposes_asset_id(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get(f"/api/v1/suites/{world['s1'].id}")
    assert resp.status_code == 200
    assert resp.json()["asset_id"] == str(world["asset_x"])


def test_run_read_exposes_asset_id(client: TestClient, world: dict[str, Any]) -> None:
    run = _seed_run(client_db(client), world["s2"], status="pass")
    _as(world["owner"])
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["asset_id"] == str(world["asset_x"])


def client_db(client: TestClient) -> Any:
    """The db_session the client's get_db override is bound to."""
    return app.dependency_overrides[get_db]()


def test_column_lineage_redacts_pairs_on_inaccessible_edges(
    client: TestClient, world: dict[str, Any]
) -> None:
    """#901 + #845 at the wire: an edge's column pairs are schema disclosure, so they
    follow the SAME one-rule as the node identity — a redacted endpoint collapses the
    mapping to a count-only box, and the column names never cross the wire. An
    accessible edge returns the full pairs (and the same count), so the redacted and
    open renderings are provably the same data minus the names."""
    db = client_db(client)
    stranger = _user(db, "stranger3@example.com")
    conn = _connection(db, stranger)
    secret = _suite(db, stranger, conn, name="Secret cols", table="MART_SECRET_COLS")
    assert secret.asset_id is not None
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_x"],
            downstream_asset_id=secret.asset_id,
            source="unity_catalog",
            connection_id=conn.id,
            columns=[["comment", "sentiment_secret_col"], ["customer_id", "customer_id"]],
        )
    )
    # A fully-visible edge with column data: asset_x -> asset_y (both the owner's).
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_x"],
            downstream_asset_id=world["asset_y"],
            source="unity_catalog",
            connection_id=conn.id,
            columns=[["order_id", "order_id"]],
        )
    )
    db.commit()

    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200
    edges = {(e["source"], e["target"]): e for e in resp.json()["lineage_edges"]}

    redacted = edges[(str(world["asset_x"]), str(secret.asset_id))]
    assert redacted["columns"] is None
    assert redacted["column_count"] == 2
    # The wire itself: a hidden column name must survive nowhere in the body.
    assert "sentiment_secret_col" not in resp.text

    visible = edges[(str(world["asset_x"]), str(world["asset_y"]))]
    assert visible["columns"] == [["order_id", "order_id"]]
    assert visible["column_count"] == 1

    # A table-grain-only edge carries neither field populated.
    for e in resp.json()["lineage_edges"]:
        if (e["source"], e["target"]) not in (
            (str(world["asset_x"]), str(secret.asset_id)),
            (str(world["asset_x"]), str(world["asset_y"])),
        ):
            assert e["columns"] is None and e["column_count"] is None


def test_json_null_columns_row_never_500s_the_asset_page(
    client: TestClient, world: dict[str, Any]
) -> None:
    """#907 regression, pinned at the exact defect: a `columns` value of JSON `null`
    (what the pre-fix bulk upsert wrote for every no-pairs edge — NOT SQL NULL, so it
    passes `is_not(None)` filters) must render as a no-grain edge, never 500.

    Written with a literal SQL cast because the fixed ORM type (`none_as_null=True`)
    can no longer produce the value — exactly like the 339 rows the first prod
    Snowflake refresh left behind."""
    from sqlalchemy import text as sql_text

    db = client_db(client)
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_x"],
            downstream_asset_id=world["asset_y"],
            source="snowflake",
        )
    )
    db.commit()
    db.execute(
        sql_text(
            "UPDATE lineage_edges SET columns = 'null'::jsonb "
            "WHERE upstream_asset_id = :up AND source = 'snowflake'"
        ),
        {"up": str(world["asset_x"])},
    )
    db.commit()

    _as(world["owner"])
    resp = client.get(f"/api/v1/assets/{world['asset_x']}")
    assert resp.status_code == 200
    edge = next(
        e
        for e in resp.json()["lineage_edges"]
        if (e["source"], e["target"]) == (str(world["asset_x"]), str(world["asset_y"]))
    )
    assert edge["columns"] is None and edge["column_count"] is None


def test_orm_none_columns_persists_as_sql_null(client: TestClient, world: dict[str, Any]) -> None:
    """#907 write side: a no-pairs edge written through the model must store SQL NULL
    (queryable with IS NULL), not JSON null — `none_as_null=True` pinned."""
    from sqlalchemy import text as sql_text

    db = client_db(client)
    db.add(
        LineageEdge(
            upstream_asset_id=world["asset_y"],
            downstream_asset_id=world["asset_x"],
            source="snowflake",
            columns=None,
        )
    )
    db.commit()
    n = db.execute(
        sql_text(
            "SELECT count(*) FROM lineage_edges "
            "WHERE upstream_asset_id = :up AND source = 'snowflake' AND columns IS NULL"
        ),
        {"up": str(world["asset_y"])},
    ).scalar_one()
    assert n == 1


def test_browse_includes_out_of_grant_assets_as_redacted_rows(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    """#920 (user-directed): browse INCLUDES assets monitored solely by suites the
    caller can't see — as redacted rows (the tree-level #845 rule: omission asserts
    "this schema holds nothing else"). Pinned at the wire: only id + namespace +
    the PARENT path cross; the leaf name, env, and every health fact stay home."""
    db = client_db(client)
    stranger = _user(db, "stranger4@example.com")
    conn = _connection(db, stranger)
    secret = _suite(db, stranger, conn, name="Secret browse", table="MART_SECRET_BROWSE")
    assert secret.asset_id is not None
    secret_asset = db.get(Asset, secret.asset_id)
    assert secret_asset is not None
    db.commit()

    _as(world["owner"])
    resp = client.get("/api/v1/assets", params={"limit": 200})
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}

    row = rows[str(secret.asset_id)]
    assert row["is_accessible"] is False
    assert row["name"] is None and row["env"] is None and row["description"] is None
    assert row["owner_user_id"] is None
    # Placement is the deliberate disclosure: namespace + the non-leaf path only.
    assert row["namespace"] == secret_asset.namespace
    leaf = secret_asset.name.rsplit(".", 1)[-1]
    assert row["name_prefix"] == secret_asset.name.rsplit(".", 1)[0]
    # Health/monitoredness are facts about the hidden asset — all at empty defaults.
    assert row["suite_count"] == 0 and row["checks_total"] == 0
    assert row["worst_severity"] is None and row["has_failed_run"] is False
    # The wire itself: the leaf name must survive nowhere in the body.
    assert leaf not in resp.text

    # The owner's own assets are untouched full rows.
    mine = rows[str(world["asset_x"])]
    assert mine["is_accessible"] is True and mine["name"] is not None

    # A workspace admin still sees the full row (no redaction for include_all).
    admin = _user(db, _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    admin_rows = {r["id"]: r for r in client.get("/api/v1/assets", params={"limit": 200}).json()}
    assert admin_rows[str(secret.asset_id)]["name"] == secret_asset.name
