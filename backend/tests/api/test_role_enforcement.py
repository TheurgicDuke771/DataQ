"""Role-gated enforcement — ADR 0033 slice #741.

The behaviour-changing slice: it closes the standing hole where *any*
authenticated user could delete or re-credential the connection every suite in
the workspace ran on.

Callers here are always real principals authenticated by PAT (`as_role`), never
the ambient dev-bypass identity — which is itself a workspace admin (#741), so
using it would make every 403 assertion below pass for the wrong reason. That is
not a hypothetical: it is exactly how eight pre-existing tests in this repo
started failing when the gates landed, and each had to be re-pointed at a real
member before it proved anything again.

Four things are covered, in the order they can fail:

1. The connection matrix — including which routes deliberately stay open.
2. Suite creation, on BOTH doors (create and import).
3. The Viewer share-cap, both belts, including the cases a grant-time check
   structurally cannot cover (legacy rows, demotion after the grant).
4. MCP parity — the ACs call for it explicitly, and it is satisfied structurally
   rather than by a second set of gates.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.secret_names import connection_secret_ref
from backend.app.db.models import Connection, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import share_service, suite_authz
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}

#: Every role, so a matrix test can't silently skip a tier.
ROLES = ("admin", "member", "viewer")


class _PassAdapter:
    """Adapter stub — the gates must be reached without a live warehouse."""

    def validate_config(self, raw: dict[str, Any]) -> Any:
        return None

    def test(self, raw: dict[str, Any], secret: str) -> None:
        return None


@pytest.fixture
def secret_store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def client(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, secret_store: FakeSecretStore
) -> Iterator[TestClient]:
    from backend.app.services import connection_service as svc

    monkeypatch.setattr(svc, "get_connection_adapter", lambda _t: _PassAdapter())
    app.dependency_overrides[get_db] = lambda: db_session
    override_secret_store(app, secret_store)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(
    db_session: Any, owner: User, secret_store: FakeSecretStore | None = None
) -> Connection:
    """A saved connection. Pass `secret_store` when the test hits `/test` or
    `/reauth`, which need a real stored credential to get past the service and
    reach (or be stopped by) the role gate — without one they 502 on "no stored
    credential", which would mask the very status code under test.
    """
    conn = Connection(
        id=uuid.uuid4(),
        name=f"conn-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=dict(_SF_CONFIG),
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    if secret_store is not None:
        conn.secret_ref = connection_secret_ref(
            connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
        )
        secret_store.set(conn.secret_ref, "stored-credential")
        db_session.commit()
    return conn


def _suite(db_session: Any, owner: User, conn: Connection) -> Suite:
    suite = Suite(
        id=uuid.uuid4(),
        name=f"suite-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=owner.id,
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def _create_payload() -> dict[str, Any]:
    return {
        "name": f"c-{uuid.uuid4().hex[:8]}",
        "type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "p@ss",
    }


# ── 1. connections: mutations are Admin-only ─────────────────────────────────


@pytest.mark.parametrize("role", ROLES)
def test_create_connection_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    _, headers = as_role(role)
    resp = client.post("/api/v1/connections", json=_create_payload(), headers=headers)
    assert resp.status_code == (201 if role == "admin" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_update_connection_is_admin_only(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)
    resp = client.patch(f"/api/v1/connections/{conn.id}", json={"name": "renamed"}, headers=headers)
    assert resp.status_code == (200 if role == "admin" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_delete_connection_is_admin_only(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """The hole this slice exists to close: before #741, a `view`-only user could
    delete the connection every suite in the workspace ran on."""
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)
    resp = client.delete(f"/api/v1/connections/{conn.id}", headers=headers)
    assert resp.status_code == (204 if role == "admin" else 403)
    # And the row really is still there for the denied tiers — a 403 that deleted
    # anyway would be the only failure mode worse than a 204.
    still_there = db_session.get(Connection, conn.id)
    assert (still_there is None) is (role == "admin")


@pytest.mark.parametrize("role", ROLES)
def test_reauth_connection_is_admin_only(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """Re-auth rotates a stored credential — the same power as delete, by another
    name, which is why it is gated with it and not with `test`."""
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)
    resp = client.post(
        f"/api/v1/connections/{conn.id}/reauth", json={"secret": "new"}, headers=headers
    )
    assert resp.status_code == (200 if role == "admin" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_test_connection_is_member_plus(
    client: TestClient, db_session: Any, as_role: Any, role: str, secret_store: FakeSecretStore
) -> None:
    """Deliberately looser than create/delete (ADR 0033's matrix): a Member
    authoring a suite must be able to check that a connection works. Viewers are
    excluded — the probe opens an outbound connection with stored credentials."""
    actor, headers = as_role(role)
    conn = _connection(db_session, actor, secret_store)
    resp = client.post(f"/api/v1/connections/{conn.id}/test", headers=headers)
    assert resp.status_code == (403 if role == "viewer" else 200)


@pytest.mark.parametrize("role", ROLES)
def test_draft_connection_test_is_member_plus(client: TestClient, as_role: Any, role: str) -> None:
    """The draft probe carries a credential in the REQUEST, so it must not be
    more permissive than the saved one. Same tier, verified separately — the two
    are different handlers and a gate on one is not a gate on the other."""
    _, headers = as_role(role)
    payload = {"type": "snowflake", "env": "dev", "config": dict(_SF_CONFIG), "secret": "p"}
    resp = client.post("/api/v1/connections/test", json=payload, headers=headers)
    assert resp.status_code == (403 if role == "viewer" else 200)


@pytest.mark.parametrize("role", ROLES)
def test_connection_reads_stay_open_to_every_tier(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """The deliberate non-gate. Members reference connections when authoring
    suites, and no tier can read a credential back out at all (`has_secret`
    only), so widening reads costs nothing the Admin gate is protecting.

    Asserted rather than left implicit: "we chose not to gate this" and "we
    forgot to gate this" look identical in a diff.
    """
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)

    listed = client.get("/api/v1/connections", headers=headers)
    fetched = client.get(f"/api/v1/connections/{conn.id}", headers=headers)
    versions = client.get(f"/api/v1/connections/{conn.id}/versions", headers=headers)

    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert versions.status_code == 200
    assert "secret" not in fetched.json()


def test_role_cannot_be_spoofed_through_the_request_body(client: TestClient, as_role: Any) -> None:
    """Adversarial: the role is read from the authenticated principal, never from
    input. A `role` field in the payload must not be honoured — and, because the
    create schema forbids extras, must not be quietly ignored either."""
    _, headers = as_role("viewer")
    payload = _create_payload() | {"role": "admin"}
    resp = client.post("/api/v1/connections", json=payload, headers=headers)
    assert resp.status_code in (403, 422)
    assert resp.status_code != 201


# ── 2. suite creation requires Member+ ───────────────────────────────────────


@pytest.mark.parametrize("role", ROLES)
def test_create_suite_requires_member(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)
    resp = client.post(
        "/api/v1/suites",
        json={"name": f"s-{uuid.uuid4().hex[:6]}", "connection_id": str(conn.id)},
        headers=headers,
    )
    assert resp.status_code == (403 if role == "viewer" else 201)


@pytest.mark.parametrize("role", ROLES)
def test_import_suite_requires_member(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """Import is the SECOND door onto suite creation. A role gate applied to only
    one of two doors is not a gate — and a Viewer who imported would become an
    owner, which the capability matrix forbids outright."""
    actor, headers = as_role(role)
    conn = _connection(db_session, actor)
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": str(conn.id),
            "document": {"version": 1, "name": f"i-{uuid.uuid4().hex[:6]}", "checks": []},
        },
        headers=headers,
    )
    assert (resp.status_code == 403) is (role == "viewer")


# ── 3. the Viewer share-cap — both belts ─────────────────────────────────────


def test_granting_edit_to_a_viewer_is_rejected(db_session: Any, as_role: Any) -> None:
    """Belt one, at grant time: the admin doing the granting gets an explanatory
    error instead of a grant that silently does nothing."""
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))

    with pytest.raises(share_service.ShareTargetInvalidError) as exc:
        share_service.grant_share(
            db_session,
            suite.id,
            actor_id=owner.id,
            target_user_id=viewer.id,
            permission="edit",
        )
    assert exc.value.detail["role"] == "viewer"


def test_granting_view_to_a_viewer_is_allowed(db_session: Any, as_role: Any) -> None:
    """The cap is on `edit`, not on sharing — a Viewer's whole purpose is to be
    given read access."""
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))

    share = share_service.grant_share(
        db_session, suite.id, actor_id=owner.id, target_user_id=viewer.id, permission="view"
    )
    assert share.permission == "view"


def test_updating_a_share_to_edit_for_a_viewer_is_rejected(db_session: Any, as_role: Any) -> None:
    """PATCH is the other door onto the same grant."""
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    share_service.grant_share(
        db_session, suite.id, actor_id=owner.id, target_user_id=viewer.id, permission="view"
    )

    with pytest.raises(share_service.ShareTargetInvalidError):
        share_service.update_share(
            db_session, suite.id, viewer.id, actor_id=owner.id, permission="edit"
        )


def test_a_legacy_edit_share_is_capped_at_view(db_session: Any, as_role: Any) -> None:
    """Belt two, the case belt one structurally cannot reach: a row that already
    exists. Written straight to the table, exactly as a pre-#741 grant would be."""
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="edit"))
    db_session.commit()

    assert suite_authz.effective_permission(db_session, suite, viewer.id) == "view"


def test_demotion_after_a_grant_takes_effect_immediately(db_session: Any, as_role: Any) -> None:
    """The case that makes the cap load-bearing rather than belt-and-braces.

    Roles resolve per request precisely so a demotion lands on the next request
    (ADR 0033 decision 7). A demotion that left a stale `edit` share live would
    make that guarantee false exactly where it matters most — no share row is
    rewritten here, only the role.
    """
    owner, _ = as_role("admin")
    member, _ = as_role("member")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    share_service.grant_share(
        db_session, suite.id, actor_id=owner.id, target_user_id=member.id, permission="edit"
    )
    assert suite_authz.effective_permission(db_session, suite, member.id) == "edit"

    member.role = "viewer"
    db_session.commit()

    assert suite_authz.effective_permission(db_session, suite, member.id) == "view"


def test_a_demoted_owner_keeps_view_not_owner(db_session: Any, as_role: Any) -> None:
    """A Viewer who created a suite before being demoted is still read-only —
    that is what the tier means. They keep `view` rather than losing the suite
    entirely, because existence-hiding would be a worse surprise than losing the
    buttons; an admin (implicit on every suite) can still manage it."""
    creator, _ = as_role("member")
    suite = _suite(db_session, creator, _connection(db_session, creator))
    assert suite_authz.effective_permission(db_session, suite, creator.id) == "owner"

    creator.role = "viewer"
    db_session.commit()

    assert suite_authz.effective_permission(db_session, suite, creator.id) == "view"


@pytest.mark.parametrize("role", ROLES)
def test_batch_and_single_agree_for_every_role(db_session: Any, as_role: Any, role: str) -> None:
    """`effective_permissions` (which stamps the suites LIST) must agree with
    `effective_permission` (which the detail view and every gate use).

    Not a redundant assertion: a list offering Edit and Delete on suites whose
    detail view then 403s is worse than no cap at all — the user is told they can
    do something the server has already decided they cannot.
    """
    owner, _ = as_role("admin")
    actor, _ = as_role(role)
    conn = _connection(db_session, owner)
    owned = _suite(db_session, actor, conn)
    shared = _suite(db_session, owner, conn)
    unrelated = _suite(db_session, owner, conn)
    db_session.add(Share(suite_id=shared.id, user_id=actor.id, permission="edit"))
    db_session.commit()

    suites = [owned, shared, unrelated]
    batch = suite_authz.effective_permissions(db_session, suites, actor.id)
    single = {s.id: suite_authz.effective_permission(db_session, s, actor.id) for s in suites}
    assert batch == single


@pytest.mark.parametrize("role", ROLES)
def test_a_viewer_cannot_reach_an_edit_gated_endpoint(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """The cap propagates to every `edit`-gated surface for free — checks,
    schedules, trigger bindings, notifications and run-triggering all gate through
    `require_permission`. Asserted on one of them so the propagation is proven
    rather than assumed; a Viewer holding a legacy `edit` share is the case."""
    owner, _ = as_role("admin")
    actor, headers = as_role(role)
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.add(Share(suite_id=suite.id, user_id=actor.id, permission="edit"))
    db_session.commit()

    resp = client.post(f"/api/v1/suites/{suite.id}/run", headers=headers)
    # Viewer → capped to `view` → 403. Member keeps `edit`; admin is implicit admin.
    assert (resp.status_code == 403) is (role == "viewer")


# ── 4. MCP parity ────────────────────────────────────────────────────────────


def test_mcp_exposes_no_connection_mutation_or_suite_create_tool() -> None:
    """The ACs ask us to verify this stays true rather than to build a gate.

    #741's new gates live on REST routes; if MCP ever grew a tool that created a
    suite or mutated a connection, it would bypass them entirely — the tool layer
    calls services directly. This is the tripwire for that, and it fails when
    someone adds such a tool without also adding the gate.
    """
    from backend.app.mcp import server as mcp_server

    names = {
        name
        for name in dir(mcp_server)
        if not name.startswith("_") and callable(getattr(mcp_server, name, None))
    }
    forbidden = {
        "create_connection",
        "update_connection",
        "delete_connection",
        "reauth_connection",
        "create_suite",
        "import_suite",
        "grant_share",
    }
    assert not (names & forbidden), (
        "an MCP tool now performs an action #741 gates on the REST side — add the "
        "equivalent role gate before exposing it"
    )


def test_a_viewer_pat_cannot_trigger_a_run_over_mcp(db_session: Any, as_role: Any) -> None:
    """MCP parity for the gate that DOES exist there.

    `trigger_suite_run` and `create_check` gate on `require_permission(edit)`,
    so the Viewer cap reaches them through the shared primitive rather than
    through a second, drift-prone set of checks. Exercised at that primitive with
    a Viewer holding a legacy `edit` share — the exact state a naive
    "viewers never have edit" assumption would get wrong.
    """
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="edit"))
    db_session.commit()

    with pytest.raises(suite_authz.SuiteForbiddenError):
        suite_authz.require_permission(db_session, suite.id, viewer.id, minimum="edit")
