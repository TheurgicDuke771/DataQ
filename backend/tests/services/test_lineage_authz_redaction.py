"""A lineage neighbour outside the caller's grants is REDACTED, not handed over (#845).

Found in prod: a non-admin viewing a shared asset saw a downstream mart in the lineage
graph, clicked it, and got "Failed to load asset: asset not found". Two defects behind
that one click:

1. the graph offered a **dead link** — the walk is not authz-scoped, but the asset
   endpoint 404s anything outside the caller's grants;
2. the far worse one — the graph **defeated the no-leak 404**. ADR 0034 decision 5 says
   an asset outside your grants is "404-no-leak", and the endpoint is carefully built so
   it cannot confirm such an asset exists. The graph confirmed it anyway, handing over
   the name, namespace, env and monitored-status one click earlier.

The fix keeps the node (dropping it would assert "nothing consumes this table" — false,
and the same confident-empty-state lie #828/#823 were about) but strips its identity
**server-side**: a name hidden in CSS has still crossed the wire.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from backend.app.db.models import Asset, LineageEdge, Suite, User
from backend.app.services import asset_view_service as svc
from backend.app.services import share_service, suite_service
from backend.app.services.asset_view_service import AssetNotFoundError
from backend.tests.services.test_asset_view_service import _conn, _user

_SECRET_NAME = "MART_ORDER_REVENUE"
_SECRET_NS = "snowflake://acme-secret-account"


def _grant(db: Any, suite: Suite, owner: User, target: User) -> None:
    """Give ``target`` view on ``suite`` (the ADR-0027 share ladder)."""
    share_service.grant_share(
        db, suite.id, actor_id=owner.id, target_user_id=target.id, permission="view"
    )
    db.commit()


def _suite_on(db: Any, owner: User, *, table: str) -> Suite:
    conn = _conn(db, owner)
    suite = suite_service.create_suite(
        db,
        name=f"S-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )
    db.commit()
    assert suite.asset_id is not None
    return suite


def _restricted_downstream(db: Any, upstream_asset_id: uuid.UUID) -> Asset:
    """A real asset downstream of ``upstream_asset_id``, targeted by a suite the viewer
    will NOT be granted — i.e. exactly the prod shape (Order Header → the mart)."""
    stranger = _user(db)
    mart_suite = _suite_on(db, stranger, table=_SECRET_NAME)
    mart = cast(Asset, db.get(Asset, mart_suite.asset_id))
    mart.namespace = _SECRET_NS
    mart.name = _SECRET_NAME
    db.add(
        LineageEdge(
            upstream_asset_id=upstream_asset_id,
            downstream_asset_id=mart.id,
            source="dbt",
        )
    )
    db.commit()
    return mart


@pytest.fixture
def scenario(db_session: Any) -> tuple[User, Asset, Asset]:
    """Olivia can view ORDER_HEADER (shared with her). A mart she has no grant for sits
    downstream of it."""
    owner = _user(db_session)
    olivia = _user(db_session)
    header_suite = _suite_on(db_session, owner, table="ORDER_HEADER")
    _grant(db_session, header_suite, owner, olivia)
    header = cast(Asset, db_session.get(Asset, header_suite.asset_id))
    mart = _restricted_downstream(db_session, header.id)
    return olivia, header, mart


def test_the_restricted_neighbour_is_still_shown(db_session: Any, scenario: Any) -> None:
    """It must NOT vanish. An empty downstream would tell Olivia "nothing consumes this
    table" — false, and the exact confident-empty-state lie #828/#823 were about. She is
    entitled to know a consumer exists; that is the blast radius."""
    olivia, header, _ = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    assert len(detail.downstream) == 1


def test_the_restricted_neighbour_carries_no_identity(db_session: Any, scenario: Any) -> None:
    """The load-bearing assertion: the identity never crosses the service boundary."""
    olivia, header, _ = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    node = detail.downstream[0]

    assert node.is_accessible is False
    assert node.name is None
    assert node.namespace is None
    assert node.env is None
    # Whether SOMEONE ELSE monitors an asset you can't see is itself a fact about that
    # asset — so it is not reported either.
    assert node.is_monitored is False
    # The whole DTO, stringified: the secret must not survive anywhere in it (a field
    # added later that echoes the name would fail here rather than ship).
    assert _SECRET_NAME not in str(node)
    assert _SECRET_NS not in str(node)


def test_the_redacted_node_is_the_one_the_endpoint_refuses(db_session: Any, scenario: Any) -> None:
    """The two rules must agree. If the graph ever offers a node the endpoint 404s, we
    are back to the dead link that surfaced this bug — so pin them together."""
    olivia, header, mart = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    assert detail.downstream[0].id == mart.id  # the id stays: the edges reference it

    with pytest.raises(AssetNotFoundError):
        svc.get_visible_asset(db_session, mart.id, user_id=olivia.id)


def test_the_edge_survives_redaction(db_session: Any, scenario: Any) -> None:
    """The graph's *shape* is what makes the count meaningful — an edge to a redacted
    node must still be drawn, or the placeholder floats disconnected."""
    olivia, header, mart = scenario
    detail = svc.get_visible_asset(db_session, header.id, user_id=olivia.id)
    assert (header.id, mart.id) in [(e.source, e.target) for e in detail.lineage_edges]


def test_a_grantee_sees_the_neighbour_in_full(db_session: Any) -> None:
    """Redaction is per-viewer, not a property of the node: share the mart's suite with
    Olivia and the same node comes back named and openable."""
    owner = _user(db_session)
    olivia = _user(db_session)
    header_suite = _suite_on(db_session, owner, table="ORDER_HEADER")
    _grant(db_session, header_suite, owner, olivia)
    header = cast(Asset, db_session.get(Asset, header_suite.asset_id))

    mart_suite = _suite_on(db_session, owner, table=_SECRET_NAME)
    mart = cast(Asset, db_session.get(Asset, mart_suite.asset_id))
    db_session.add(
        LineageEdge(upstream_asset_id=header.id, downstream_asset_id=mart.id, source="dbt")
    )
    _grant(db_session, mart_suite, owner, olivia)
    db_session.commit()

    node = svc.get_visible_asset(db_session, header.id, user_id=olivia.id).downstream[0]
    assert node.is_accessible is True
    assert node.name is not None and _SECRET_NAME in node.name
    assert node.is_monitored is True


@pytest.mark.parametrize("include_all", [False, True])
def test_the_three_surfaces_agree_on_what_is_visible(db_session: Any, include_all: bool) -> None:
    """Browse, the detail endpoint, and the lineage graph must derive ACCESSIBILITY from
    the SAME rule — over every asset kind. (#920 changed browse's *presentation* of an
    inaccessible asset — a redacted row instead of omission — but the rule is one.)

    This is the regression that matters. The bug being fixed here *was* a disagreement
    between two of these surfaces: the graph offered a node the endpoint refused. They
    agree today; nothing but this test stops a future edit to one of the three
    (a SQL predicate, an imperative guard, a set expression) from re-opening the gap.
    """
    owner = _user(db_session)
    viewer = _user(db_session)
    stranger = _user(db_session)

    granted = _suite_on(db_session, owner, table="GRANTED")
    _grant(db_session, granted, owner, viewer)
    ungranted = _suite_on(db_session, stranger, table="UNGRANTED")  # suites, none viewer's
    suiteless = Asset(namespace=_SECRET_NS, name="SUITELESS", env="dev")
    db_session.add(suiteless)
    db_session.commit()

    kinds = {
        "granted": cast(uuid.UUID, granted.asset_id),
        "ungranted": cast(uuid.UUID, ungranted.asset_id),
        "suiteless": suiteless.id,
    }
    user_id = stranger.id if include_all else viewer.id  # admin identity is irrelevant

    rows_by_id = {
        row.id: row
        for row in svc.list_visible_assets(db_session, user_id=user_id, include_all=include_all)
    }
    for kind, asset_id in kinds.items():
        # 1) the detail endpoint
        try:
            svc.get_visible_asset(db_session, asset_id, user_id=user_id, include_all=include_all)
            openable = True
        except AssetNotFoundError:
            openable = False
        # 2) browse — since #920 EVERY asset is listed; accessibility is the row's
        #    redaction state (an ungranted asset appears anonymous, never omitted).
        row = rows_by_id.get(asset_id)
        assert row is not None, f"{kind}: browse omitted the asset (include_all={include_all})"
        browse_accessible = row.is_accessible
        # 3) the lineage graph's own accessibility derivation
        graph_accessible = asset_id in svc._accessible_asset_ids(
            db_session,
            [asset_id],
            user_id=user_id,
            include_all=include_all,
            has_suite=svc._monitored_ids(db_session, [asset_id]),
        )

        expected = include_all or kind != "ungranted"
        assert openable is expected, f"{kind}: detail disagrees (include_all={include_all})"
        assert (
            browse_accessible is expected
        ), f"{kind}: browse disagrees (include_all={include_all})"
        assert graph_accessible is expected, f"{kind}: graph disagrees (include_all={include_all})"
        # The redacted browse row and the redacted graph node tell the same lie-free
        # story: existence yes, identity no.
        if not expected:
            assert row.name is None and row.env is None


def test_a_workspace_admin_sees_everything_unredacted(db_session: Any, scenario: Any) -> None:
    """An admin's visibility is workspace-wide (ADR 0027) — redaction must not apply."""
    _, header, mart = scenario
    admin = _user(db_session)
    detail = svc.get_visible_asset(db_session, header.id, user_id=admin.id, include_all=True)
    node = detail.downstream[0]
    assert node.is_accessible is True
    assert node.name == _SECRET_NAME  # the fixture pinned the identity verbatim
    assert node.id == mart.id
