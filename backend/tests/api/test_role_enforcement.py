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
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.secret_names import connection_secret_ref
from backend.app.db.models import Check, Connection, Run, Schedule, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import share_service, suite_authz
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store
from backend.tests.support.mcp_gates import (
    member_denied_tools,
    outsider_denied_tools,
    viewer_denied_tools,
)

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}

#: Sentinels for probes needing a REAL row, substituted in `_assert_tool_denies`.
#:
#: Both exist for the same reason: these tools look the row up BEFORE gating, so a
#: fabricated id returns "run not found" / "check not found" — both in the
#: accepted-denial vocabulary — and the sweep passes without the authz gate ever
#: being reached. The run sentinel was added when that was noticed for `run_id`;
#: the check one was missed until a reviewer mutation-verified it, which is the
#: whole argument for checking that a guard can fail rather than trusting it.
_REAL_RUN = "<a real run, substituted at probe time>"
_REAL_CHECK = "<a real check, substituted at probe time>"
_REAL_SCHEDULE = "<a real schedule, substituted at probe time>"
_REAL_CONNECTION = "<a real connection, substituted at probe time>"

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
def test_draft_connection_test_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    """STRICTER than the saved-connection `/test` beside it, and deliberately.

    The saved probe uses config an admin already stored; this one probes config
    the CALLER supplies — including `*_secret_name` fields resolved against the
    flat SecretStore namespace, which is #1118 (name a victim connection's secret,
    point the endpoint at a host you control). Member+ would have handed that
    surface to exactly the tier this slice just denied connection-write to, in
    support of a Create form that tier cannot open.
    """
    _, headers = as_role(role)
    payload = {"type": "snowflake", "env": "dev", "config": dict(_SF_CONFIG), "secret": "p"}
    resp = client.post("/api/v1/connections/test", json=payload, headers=headers)
    assert resp.status_code == (200 if role == "admin" else 403)


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


@pytest.mark.parametrize("role", ROLES)
def test_the_probe_endpoint_is_admin_only(
    client: TestClient, as_role: Any, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third door, found by review rather than by the matrix.

    `POST /_probe/snowflake-suite` is mounted unconditionally and had only
    `get_current_user` on it, while `ensure_probe_fixtures` creates a
    **Connection** (Admin-only) *and* a caller-owned **Suite** (Member+) and then
    dispatches a run. A Viewer calling it would have obtained both, straight
    around the two gates this slice adds.

    Gated at the stricter of the two things it does. This is the shape worth
    remembering: the gates went on the resources' *own* routes, and a sibling
    endpoint that creates the same resources by another name was invisible to
    that reasoning.
    """
    _, headers = as_role(role)
    resp = client.post("/api/v1/_probe/snowflake-suite", headers=headers)
    # Admin gets past the gate (whatever the probe then does about a missing
    # warehouse); the other tiers must not.
    assert (resp.status_code == 403) is (role != "admin")


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


def test_every_mcp_tool_declares_a_gate_and_the_registry_matches() -> None:
    """The tripwire for #741's blind spot: MCP calls services DIRECTLY.

    Every role gate #741 added lives on a REST route. A new MCP tool that created
    a suite or mutated a connection would bypass all of them silently — the tool
    layer never touches the router.

    Asserted as an **exact set** against `support/mcp_gates.GATES`, not by
    intersecting with a list of forbidden names. A name list only catches the
    names someone thought of: `add_connection` or `new_suite` would sail straight
    through. An exact set fails on ANY new tool, which is the point.

    The gate now lives in that table as **data rather than a comment**, because
    the old bare set of names could catch a tool being added but not a tool being
    added with the *wrong* gate — a name in the wrong comment group looks exactly
    like a name in the right one. Declaring it makes the sweep below possible:
    adding a row here is adding an enforcement test, not just a manifest entry.

    If you are adding a tool: add it to `GATES` with the gate it actually calls,
    and make sure that is one of (a) read-only, (b) `suite_authz.require_permission`,
    or (c) `server._require_role`.
    """
    import asyncio

    from backend.app.mcp.server import mcp
    from backend.tests.support.mcp_gates import GATES

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert tools == set(GATES)


def test_every_declared_gate_is_a_known_gate() -> None:
    """A typo'd gate value would match no sweep and therefore be enforced by
    nothing — while still passing the registry tripwire, which only compares
    keys. Cheap guard against the table quietly opting a tool out."""
    from backend.tests.support.mcp_gates import GATES, KNOWN_GATES

    unknown = {name: gate for name, gate in GATES.items() if gate not in KNOWN_GATES}
    assert not unknown, unknown


def test_read_gated_tools_really_take_no_suite_id() -> None:
    """`read` is the one gate with no denial to assert, which makes it the one
    place a tool could be parked to escape every sweep. It is only defensible for
    a tool that has no suite to gate ON — so that is what is checked, against the
    advertised schema."""
    import asyncio

    from backend.app.mcp.server import mcp
    from backend.tests.support.mcp_gates import tools_with_gate

    read_only = set(tools_with_gate("read"))
    for tool in asyncio.run(mcp.list_tools()):
        if tool.name not in read_only:
            continue
        params = set(tool.parameters.get("properties", {}))
        assert "suite_id" not in params, (
            f"{tool.name} is declared `read` but accepts a suite_id — it must "
            "either gate on it (`read:suite-optional`) or not take it"
        )


@pytest.mark.parametrize("tool_name", viewer_denied_tools())
def test_no_mcp_tool_that_should_deny_a_viewer_lets_one_through(
    db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """The RBAC sweep: EVERY Viewer-denied tool, not one representative sample.

    This used to test `trigger_suite_run` alone and reason that the rest were
    covered "for free" because they call the same primitive. That reasoning is
    right about the primitive and says nothing about whether a given tool
    actually calls it — which is the only thing that can regress.

    The Viewer holds a **legacy `edit` share**: the exact state a naive "viewers
    never have edit" assumption gets wrong, and the reason the cap is enforced at
    the point of use rather than only when a share is granted.
    """
    owner, _ = as_role("admin")
    viewer, _ = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="edit"))
    db_session.commit()

    _assert_tool_denies(monkeypatch, db_session, viewer, tool_name, suite)


def test_mcp_exposes_no_admin_only_capability_by_design() -> None:
    """The `role:admin` sweep below is empty, and that is the invariant — not an
    oversight.

    Every Admin-only capability in ADR 0033's matrix is a connection *mutation*
    (create / edit / delete / re-auth), and MCP deliberately exposes none of them:
    a credential must never transit an LLM (#529's standing exclusion). So there
    is nothing for a Member sweep to sweep.

    Stated as a test so the empty parametrize reads as a decision rather than a
    forgotten row — and the sweep activates the moment that changes.
    """
    from backend.tests.support.mcp_gates import member_denied_tools as _admin_only

    assert _admin_only() == [], (
        "an admin-only MCP tool was added — that is a real decision (a credential "
        "must never transit an LLM), so revisit it here deliberately"
    )


@pytest.mark.parametrize("tool_name", member_denied_tools())
def test_no_admin_only_mcp_tool_lets_a_member_through(
    db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """`role:admin` needs its own principal, not the Viewer sweep's.

    A tool declared `role:admin` but implemented with `minimum="member"` refuses a
    Viewer perfectly well — so the Viewer sweep passes, and every Member in the
    workspace can invoke an admin capability. Only a Member can tell the two
    apart. The Member is even given an `edit` share, so a suite-ladder gate
    cannot stand in for the role gate this row is asserting.
    """
    owner, _ = as_role("admin")
    member, _ = as_role("member")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.add(Share(suite_id=suite.id, user_id=member.id, permission="edit"))
    db_session.commit()

    _assert_tool_denies(monkeypatch, db_session, member, tool_name, suite)


@pytest.mark.parametrize("tool_name", outsider_denied_tools())
def test_suite_scoped_mcp_tools_deny_a_user_with_no_share(
    db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """The other half: a Member in good standing, with no share on THIS suite.

    Without this, a tool declared `suite:view` and gating on nothing at all would
    pass every other sweep — a Viewer is not refused by a read they are entitled
    to, so the Viewer sweep says nothing about view-gated tools. This is also what
    covers `read:suite-optional`'s up-front gate, whose absence turns a denial
    into a misleading empty list (#828).
    """
    owner, _ = as_role("admin")
    outsider, _ = as_role("member")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    db_session.commit()

    _assert_tool_denies(monkeypatch, db_session, outsider, tool_name, suite)


def _assert_tool_denies(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
    principal: User,
    tool_name: str,
    suite: Suite,
) -> None:
    """Enter the real tool as `principal` and assert it refuses, for the right reason."""
    from contextlib import contextmanager

    from backend.app.mcp import server as mcp_server

    # Built BEFORE `pytest.raises`, deliberately: an unknown tool raises here, and
    # inside the block that failure would be swallowed and re-reported as "denied
    # for the wrong reason" — a missing probe silently reading as a pass.
    args = _viewer_probe_args(tool_name, suite)
    if args.get("run_id") == _REAL_RUN:
        run = Run(suite_id=suite.id, status="succeeded")
        db_session.add(run)
        db_session.commit()
        args = {**args, "run_id": str(run.id)}
    if args.get("connection_id") == _REAL_CONNECTION:
        args = {**args, "connection_id": str(suite.connection_id)}
    if args.get("schedule_id") == _REAL_SCHEDULE:
        schedule = Schedule(
            suite_id=suite.id,
            cron="0 2 * * *",
            timezone="UTC",
            next_run_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=suite.created_by,
        )
        db_session.add(schedule)
        db_session.commit()
        args = {**args, "schedule_id": str(schedule.id)}
    if args.get("check_id") == _REAL_CHECK:
        check = Check(
            suite_id=suite.id,
            name="authz probe target",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "EMAIL"},
        )
        db_session.add(check)
        db_session.commit()
        args = {**args, "check_id": str(check.id)}

    @contextmanager
    def _as_principal() -> Any:
        yield db_session, principal

    monkeypatch.setattr(mcp_server, "_ctx", _as_principal)
    tool = getattr(mcp_server, tool_name)
    with pytest.raises(Exception) as exc:
        tool(**args)

    message = str(exc.value).lower()
    # Either axis is an acceptable denial — the suite ladder's forbidden/not-found,
    # or the coarse role gate's. What is NOT acceptable is the call succeeding, or
    # failing for an unrelated reason (a missing argument, a bad UUID), which would
    # make this pass while proving nothing.
    assert any(
        word in message for word in ("forbidden", "permission", "workspace role", "not found")
    ), f"{tool_name} denied for the wrong reason: {exc.value}"


def _viewer_probe_args(tool_name: str, suite: Suite) -> dict[str, Any]:
    """Minimal valid arguments per tool, so a sweep reaches the gate.

    Deliberately valid: the point is that the call is stopped by *authorization*,
    not by argument validation. Arguments that would 422 first would make every
    row pass regardless of the gate.

    A `raise`, not an `assert`: an assert vanishes under `-O`, and this is the
    guard that stops a newly-declared tool from being silently skipped.
    """
    sid = str(suite.id)
    per_tool: dict[str, dict[str, Any]] = {
        # suite:edit
        "trigger_suite_run": {"suite_id": sid},
        "create_check": {
            "suite_id": sid,
            "name": "authz probe",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "EMAIL"},
        },
        "profile_column": {"suite_id": sid, "columns": ["EMAIL"]},
        "cancel_run": {"run_id": _REAL_RUN},
        "suggest_column_policy": {"suite_id": sid},
        "update_suite": {"suite_id": sid, "name": "renamed by probe"},
        "get_column_policy": {"suite_id": sid},
        "set_column_policy": {"suite_id": sid, "pii_columns": ["EMAIL"]},
        # role:member — no suite argument at all, which is the point: these are
        # the capabilities with no resource ladder to ride.
        "test_connection": {"connection_id": _REAL_CONNECTION},
        "import_suite": {
            "connection_id": _REAL_CONNECTION,
            "name": "imported probe",
            "checks": [],
        },
        "create_schedule": {"suite_id": sid, "cron": "0 2 * * *"},
        "delete_schedule": {"schedule_id": _REAL_SCHEDULE},
        "create_trigger_binding": {
            "provider": "adf",
            "pipeline_or_dag_id": "pl_probe",
            "env": "dev",
            "suite_id": sid,
        },
        "update_check": {"suite_id": sid, "check_id": _REAL_CHECK, "name": "renamed"},
        "delete_check": {"suite_id": sid, "check_id": _REAL_CHECK},
        "snooze_check": {"suite_id": sid, "check_id": _REAL_CHECK, "hours": 2},
        "dryrun_check": {
            "suite_id": sid,
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "EMAIL"},
        },
        # suite:view
        "export_suite": {"suite_id": sid},
        "get_check": {"suite_id": sid, "check_id": _REAL_CHECK},
        "get_check_history": {"suite_id": sid, "check_id": _REAL_CHECK},
        "get_notification_config": {"suite_id": sid},
        "get_suite_results": {"suite_id": sid},
        "list_checks": {"suite_id": sid},
        # These two take a RUN id and gate on the run's OWN suite. The sentinel is
        # replaced with a real run by `_assert_tool_denies`: a fabricated id would
        # return "run not found" before authz — an accepted denial word — and pass
        # this test vacuously.
        "get_run_results": {"run_id": _REAL_RUN},
        "get_run_status": {"run_id": _REAL_RUN},
        # read:suite-optional — the named-suite half is what has a gate
        "list_runs": {"suite_id": sid},
        "list_schedules": {"suite_id": sid},
        "list_trigger_bindings": {"suite_id": sid},
    }
    if tool_name not in per_tool:
        raise AssertionError(
            f"{tool_name} is declared gated in mcp_gates.GATES but has no probe "
            "arguments here — add them, or the sweep silently skips it"
        )
    return per_tool[tool_name]
